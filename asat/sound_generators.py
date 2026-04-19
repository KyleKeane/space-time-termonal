"""Sound generators: turn SoundRecipe records into AudioBuffer waveforms.

A generator consumes one `SoundRecipe` and produces a mono `AudioBuffer`
at a caller-specified sample rate. Spatialization (azimuth / elevation)
happens later in the pipeline, so generators deliberately stay mono;
the recipe's `volume` field is applied as a linear gain before the
buffer leaves the generator so downstream stages see the cue at its
intended loudness.

Four generators ship with A2, one per supported `SoundRecipe.kind`:

- ToneGenerator    (kind="tone")    synth sine / square / triangle /
                                    sawtooth tones with attack+release.
- ChordGenerator   (kind="chord")   sum of tones at multiple
                                    frequencies, normalised to avoid
                                    clipping.
- SampleGenerator  (kind="sample")  loads a .wav file, converts it to
                                    the target sample rate by linear
                                    resampling, trims start/end, and
                                    optionally loops.
- SilenceGenerator (kind="silence") a well-formed silent buffer of a
                                    given duration; useful as a pause
                                    or a placeholder in A/B tests.

The generators are deliberately independent of the rest of the audio
stack: they do not touch the event bus, do not subscribe to anything,
and never call out to the speech stack. A3's AudioEngine will call
them on demand as events fly past.

Synthesis uses only the standard library (`math`, `wave`) so the
project's stdlib-only posture is preserved.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any, Mapping, Protocol

from asat.audio import DEFAULT_SAMPLE_RATE, AudioBuffer, ChannelLayout
from asat.sound_bank import SoundRecipe


WAVEFORMS = ("sine", "square", "triangle", "sawtooth")


class SoundGeneratorError(ValueError):
    """Raised when a SoundRecipe cannot be turned into audio."""


class SoundGenerator(Protocol):
    """Anything that can turn one recipe into a mono AudioBuffer.

    Implementations in-tree: ``ToneGenerator``, ``ChordGenerator``,
    ``SampleGenerator``, ``SilenceGenerator`` — one per ``kind`` the
    default bank uses. Pluggable: register a new generator on a
    ``SoundGeneratorRegistry`` to add a ``kind="..."`` recipe type.
    """

    def generate(self, recipe: SoundRecipe, *, sample_rate: int) -> AudioBuffer:
        """Render the recipe into a mono AudioBuffer at sample_rate."""
        ...


class ToneGenerator:
    """Render `kind="tone"` recipes.

    Params (all optional except `frequency`):
        frequency (float, Hz)   fundamental pitch.
        duration  (float, s)    default 0.15.
        waveform  (str)         one of WAVEFORMS, default "sine".
        attack    (float, s)    linear fade-in, default 0.005.
        release   (float, s)    linear fade-out, default 0.02.
    """

    def generate(self, recipe: SoundRecipe, *, sample_rate: int) -> AudioBuffer:
        _require(recipe.kind == "tone", f"ToneGenerator only handles 'tone', got {recipe.kind!r}")
        frequency = _required_positive(recipe.params, "frequency", recipe.id)
        duration = _positive(recipe.params.get("duration", 0.15), "duration", recipe.id)
        waveform = _waveform_name(recipe.params.get("waveform", "sine"), recipe.id)
        attack = _non_negative(recipe.params.get("attack", 0.005), "attack", recipe.id)
        release = _non_negative(recipe.params.get("release", 0.02), "release", recipe.id)
        _require(sample_rate > 0, "sample_rate must be positive")

        samples = _synth_tone(frequency, duration, waveform, sample_rate)
        _apply_envelope(samples, attack, release, sample_rate)
        _scale_in_place(samples, recipe.volume)
        return AudioBuffer.mono(samples, sample_rate)


class ChordGenerator:
    """Render `kind="chord"` recipes as a sum of partials.

    Params:
        frequencies (list[float])   one entry per partial, required.
        duration    (float, s)      default 0.25.
        waveform    (str)           default "sine".
        attack      (float, s)      default 0.005.
        release     (float, s)      default 0.04.

    Partials are summed then normalised by the partial count so a
    six-note chord does not clip. The caller's `volume` is applied on
    top of that.
    """

    def generate(self, recipe: SoundRecipe, *, sample_rate: int) -> AudioBuffer:
        _require(recipe.kind == "chord", f"ChordGenerator only handles 'chord', got {recipe.kind!r}")
        raw_freqs = recipe.params.get("frequencies")
        _require(
            isinstance(raw_freqs, (list, tuple)) and len(raw_freqs) > 0,
            f"chord {recipe.id!r} requires a non-empty 'frequencies' list",
        )
        frequencies = [_positive(value, f"frequencies[{index}]", recipe.id) for index, value in enumerate(raw_freqs)]
        duration = _positive(recipe.params.get("duration", 0.25), "duration", recipe.id)
        waveform = _waveform_name(recipe.params.get("waveform", "sine"), recipe.id)
        attack = _non_negative(recipe.params.get("attack", 0.005), "attack", recipe.id)
        release = _non_negative(recipe.params.get("release", 0.04), "release", recipe.id)
        _require(sample_rate > 0, "sample_rate must be positive")

        frame_count = max(1, int(duration * sample_rate))
        mixed = [0.0] * frame_count
        for frequency in frequencies:
            partial = _synth_tone(frequency, duration, waveform, sample_rate)
            for index in range(frame_count):
                mixed[index] += partial[index]
        normaliser = 1.0 / len(frequencies)
        for index in range(frame_count):
            mixed[index] *= normaliser
        _apply_envelope(mixed, attack, release, sample_rate)
        _scale_in_place(mixed, recipe.volume)
        return AudioBuffer.mono(mixed, sample_rate)


class SampleGenerator:
    """Render `kind="sample"` recipes by loading a WAV file.

    Params:
        path   (str)     filesystem path to a .wav file, required.
        start  (float)   trim N seconds from the start, default 0.
        end    (float)   stop at N seconds, default = full length.
        loop   (bool)    repeat the trimmed region to fill duration.
        duration (float) only meaningful with loop=True; target length.

    Stereo files are downmixed by averaging the two channels. Sample
    rates that do not match the engine's sample_rate are converted by
    linear interpolation so the recipe can be played alongside
    generated tones without timing drift.
    """

    def generate(self, recipe: SoundRecipe, *, sample_rate: int) -> AudioBuffer:
        _require(recipe.kind == "sample", f"SampleGenerator only handles 'sample', got {recipe.kind!r}")
        path = recipe.params.get("path")
        _require(isinstance(path, str) and path, f"sample {recipe.id!r} requires a non-empty 'path'")
        source = Path(path)
        _require(source.exists(), f"sample {recipe.id!r} path does not exist: {source}")
        _require(sample_rate > 0, "sample_rate must be positive")

        raw_samples, source_rate = _read_wav_mono(source, recipe.id)
        if source_rate != sample_rate:
            raw_samples = _linear_resample(raw_samples, source_rate, sample_rate)
        start = _non_negative(recipe.params.get("start", 0.0), "start", recipe.id)
        end_value = recipe.params.get("end")
        start_index = min(int(start * sample_rate), len(raw_samples))
        if end_value is None:
            end_index = len(raw_samples)
        else:
            end_seconds = _non_negative(end_value, "end", recipe.id)
            end_index = min(int(end_seconds * sample_rate), len(raw_samples))
        _require(end_index > start_index, f"sample {recipe.id!r} trims to an empty region")
        trimmed = raw_samples[start_index:end_index]

        if bool(recipe.params.get("loop", False)):
            target_seconds = _positive(recipe.params.get("duration", len(trimmed) / sample_rate), "duration", recipe.id)
            target_frames = max(1, int(target_seconds * sample_rate))
            output = _loop_to_length(trimmed, target_frames)
        else:
            output = list(trimmed)

        _scale_in_place(output, recipe.volume)
        return AudioBuffer.mono(output, sample_rate)


class SilenceGenerator:
    """Render `kind="silence"` recipes as a well-formed silent buffer.

    Params:
        duration (float, s)   default 0.1.
    """

    def generate(self, recipe: SoundRecipe, *, sample_rate: int) -> AudioBuffer:
        _require(recipe.kind == "silence", f"SilenceGenerator only handles 'silence', got {recipe.kind!r}")
        duration = _non_negative(recipe.params.get("duration", 0.1), "duration", recipe.id)
        _require(sample_rate > 0, "sample_rate must be positive")
        return AudioBuffer.silence(duration, sample_rate, ChannelLayout.MONO)


class SoundGeneratorRegistry:
    """Dispatch a SoundRecipe to the right SoundGenerator.

    The registry is mutable on purpose so tests and future extensions
    can register additional generators without reaching into module
    globals. The default registry knows every kind declared in
    `asat.sound_bank.SOUND_KINDS`.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._generators: dict[str, SoundGenerator] = {}

    def register(self, kind: str, generator: SoundGenerator) -> None:
        """Bind a generator to a recipe kind, overriding any prior entry."""
        self._generators[kind] = generator

    def generator_for(self, kind: str) -> SoundGenerator:
        """Return the generator for kind or raise SoundGeneratorError."""
        if kind not in self._generators:
            raise SoundGeneratorError(f"no generator registered for kind {kind!r}")
        return self._generators[kind]

    def generate(self, recipe: SoundRecipe, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> AudioBuffer:
        """Render recipe through the registered generator for its kind."""
        return self.generator_for(recipe.kind).generate(recipe, sample_rate=sample_rate)

    @classmethod
    def default(cls) -> "SoundGeneratorRegistry":
        """Return a registry pre-populated with every stock generator."""
        registry = cls()
        registry.register("tone", ToneGenerator())
        registry.register("chord", ChordGenerator())
        registry.register("sample", SampleGenerator())
        registry.register("silence", SilenceGenerator())
        return registry


def generate_sound(
    recipe: SoundRecipe,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    registry: SoundGeneratorRegistry | None = None,
) -> AudioBuffer:
    """Convenience wrapper: render a single recipe with the default registry."""
    active = registry or SoundGeneratorRegistry.default()
    return active.generate(recipe, sample_rate=sample_rate)


def _synth_tone(frequency: float, duration: float, waveform: str, sample_rate: int) -> list[float]:
    """Produce a raw unit-amplitude waveform, no envelope, no gain."""
    frame_count = max(1, int(duration * sample_rate))
    angular = 2.0 * math.pi * frequency / sample_rate
    samples: list[float] = []
    if waveform == "sine":
        for n in range(frame_count):
            samples.append(math.sin(angular * n))
    elif waveform == "square":
        for n in range(frame_count):
            samples.append(1.0 if math.sin(angular * n) >= 0 else -1.0)
    elif waveform == "triangle":
        period = sample_rate / frequency if frequency > 0 else 1.0
        for n in range(frame_count):
            phase = (n % period) / period
            samples.append(4.0 * abs(phase - 0.5) - 1.0)
    elif waveform == "sawtooth":
        period = sample_rate / frequency if frequency > 0 else 1.0
        for n in range(frame_count):
            phase = (n % period) / period
            samples.append(2.0 * phase - 1.0)
    else:
        raise SoundGeneratorError(f"unknown waveform {waveform!r}")
    return samples


def _apply_envelope(samples: list[float], attack: float, release: float, sample_rate: int) -> None:
    """Apply a linear attack-release envelope to samples in place."""
    count = len(samples)
    if count == 0:
        return
    attack_frames = min(count, int(attack * sample_rate))
    release_frames = min(count - attack_frames, int(release * sample_rate))
    for index in range(attack_frames):
        samples[index] *= index / attack_frames
    for offset in range(release_frames):
        index = count - 1 - offset
        samples[index] *= offset / release_frames if release_frames else 0.0


def _scale_in_place(samples: list[float], gain: float) -> None:
    """Multiply every sample by gain (the recipe's volume)."""
    if gain == 1.0:
        return
    for index in range(len(samples)):
        samples[index] *= gain


def _read_wav_mono(path: Path, recipe_id: str) -> tuple[list[float], int]:
    """Read a WAV file and return (mono_samples, sample_rate).

    Stereo files are downmixed by averaging the two channels. 8-bit and
    16-bit PCM are supported; other widths raise SoundGeneratorError
    because they rarely appear in practice and keeping the code narrow
    makes the round-trip obvious.
    """
    with wave.open(str(path), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        sample_rate = reader.getframerate()
        frame_count = reader.getnframes()
        raw = reader.readframes(frame_count)
    if sample_width == 2:
        divisor = 32768.0
        step = 2
    elif sample_width == 1:
        divisor = 128.0
        step = 1
    else:
        raise SoundGeneratorError(
            f"sample {recipe_id!r}: unsupported WAV sample width {sample_width}"
        )
    mono: list[float] = []
    stride = step * channels
    for frame_start in range(0, len(raw), stride):
        frame_sum = 0.0
        for channel in range(channels):
            offset = frame_start + channel * step
            if sample_width == 2:
                value = int.from_bytes(raw[offset : offset + 2], "little", signed=True)
            else:
                value = raw[offset] - 128
            frame_sum += value / divisor
        mono.append(frame_sum / channels if channels > 0 else 0.0)
    return mono, sample_rate


def _linear_resample(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    """Resample with linear interpolation. Preserves duration."""
    if source_rate == target_rate or len(samples) == 0:
        return samples
    duration = len(samples) / source_rate
    target_count = max(1, int(duration * target_rate))
    result: list[float] = []
    last_index = len(samples) - 1
    for out_index in range(target_count):
        source_position = out_index * source_rate / target_rate
        base = int(source_position)
        if base >= last_index:
            result.append(samples[last_index])
            continue
        frac = source_position - base
        result.append(samples[base] * (1.0 - frac) + samples[base + 1] * frac)
    return result


def _loop_to_length(samples: list[float], target_frames: int) -> list[float]:
    """Repeat samples until the output has target_frames; trims the tail."""
    if not samples:
        return [0.0] * target_frames
    result: list[float] = []
    while len(result) < target_frames:
        remaining = target_frames - len(result)
        result.extend(samples[:remaining])
    return result


def _require(condition: bool, message: str) -> None:
    """Raise SoundGeneratorError if condition is false."""
    if not condition:
        raise SoundGeneratorError(message)


def _required_positive(params: Mapping[str, Any], key: str, recipe_id: str) -> float:
    """Fetch params[key] and require it to be a number > 0."""
    if key not in params:
        raise SoundGeneratorError(f"recipe {recipe_id!r} missing required param {key!r}")
    return _positive(params[key], key, recipe_id)


def _positive(value: Any, label: str, recipe_id: str) -> float:
    """Coerce to float and require > 0."""
    number = _as_float(value, label, recipe_id)
    if number <= 0:
        raise SoundGeneratorError(f"recipe {recipe_id!r}: {label} must be > 0, got {number}")
    return number


def _non_negative(value: Any, label: str, recipe_id: str) -> float:
    """Coerce to float and require >= 0."""
    number = _as_float(value, label, recipe_id)
    if number < 0:
        raise SoundGeneratorError(f"recipe {recipe_id!r}: {label} must be >= 0, got {number}")
    return number


def _as_float(value: Any, label: str, recipe_id: str) -> float:
    """Best-effort float coercion with a recipe-aware error message."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SoundGeneratorError(
            f"recipe {recipe_id!r}: {label} must be a number, got {value!r}"
        ) from exc


def _waveform_name(value: Any, recipe_id: str) -> str:
    """Validate and return a supported waveform name."""
    name = str(value)
    if name not in WAVEFORMS:
        raise SoundGeneratorError(
            f"recipe {recipe_id!r}: waveform must be one of {WAVEFORMS}, got {name!r}"
        )
    return name
