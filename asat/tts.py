"""Text-to-speech abstraction.

``TTSEngine`` is a Protocol that any speech backend can satisfy. This
keeps the rest of the audio pipeline independent of which engine
synthesizes the words. ASAT ships with several reference
implementations so users can pick the one that best suits their
platform and accessibility preferences:

- ``ToneTTSEngine`` — deterministic tonal waveform, never intelligible
  speech. It is the floor of the engine stack: it runs in headless
  CI, has no dependencies, and is what tests rely on because its
  output is reproducible bit-for-bit.
- ``Pyttsx3Engine`` — wraps the ``pyttsx3`` Python package which
  routes to Windows SAPI5, macOS NSSpeechSynthesizer, and Linux
  ``espeak-ng``. One code path delivers real speech on every platform
  ASAT supports.
- ``EspeakNgEngine`` — direct subprocess to ``espeak-ng --stdout``.
  Linux-first and also available on macOS (``brew install espeak-ng``)
  and Windows builds. Exposes more low-level parameters (voice,
  speed, pitch, amplitude) than the pyttsx3 wrapper.
- ``SystemSayEngine`` — subprocess to macOS' ``say(1)`` binary.
  Native high-quality voices without a Python dep.

Each real engine exposes an ``available()`` classmethod so the
``TTSEngineRegistry`` (``asat/tts_registry.py``) can pick the best
installed backend without actually constructing one. Every subprocess
call is short and synchronous; per the project's security posture
each backend is a thin, auditable wrapper around a well-known local
engine, never a network call.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional, Protocol

from asat.audio import AudioBuffer, ChannelLayout, DEFAULT_SAMPLE_RATE, VoiceProfile


class TTSEngine(Protocol):
    """Synthesizes a line of text into a mono AudioBuffer.

    In-tree implementations: ``ToneTTSEngine`` (deterministic tonal
    stand-in, zero deps), ``Pyttsx3Engine``, ``EspeakNgEngine``,
    ``SystemSayEngine`` (real speech via pluggable backends). Pluggable:
    a new backend drops in by implementing ``synthesize`` and is
    registered via ``TTSEngineRegistry``.
    """

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Return a mono AudioBuffer containing the rendered speech."""
        ...


class ToneTTSEngine:
    """Deterministic tone-based stand-in for a real speech synthesizer.

    For each character of input text, the engine emits a short sine
    tone whose frequency is derived from the character's code point
    and the voice's base pitch. The duration of each tone is
    controlled by the voice's words-per-minute setting. The volume
    field of the voice is applied as a linear gain.

    The result is a reproducible waveform suitable for pipeline
    testing and manual sanity checks. It is not speech.
    """

    CHARACTERS_PER_WORD = 5
    SEMITONE_STEP = 0.06

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        """Store the sample rate all produced buffers will use."""
        self._sample_rate = sample_rate

    @property
    def sample_rate(self) -> int:
        """Return the sample rate of every buffer this engine produces."""
        return self._sample_rate

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Render the given text into a mono tone buffer.

        Empty or whitespace-only text returns a very short silence so
        downstream stages always receive a well-formed buffer.
        """
        if not text or text.isspace():
            return AudioBuffer.silence(0.05, self._sample_rate)
        samples_per_char = self._samples_per_character(voice)
        waveform: list[float] = []
        for index, character in enumerate(text):
            frequency = self._frequency_for_character(character, voice.pitch_hz)
            self._append_tone(waveform, frequency, samples_per_char, voice.volume)
        return AudioBuffer.mono(waveform, self._sample_rate)

    def _samples_per_character(self, voice: VoiceProfile) -> int:
        """Translate words-per-minute into samples per character."""
        effective_wpm = max(voice.speed_wpm, 30.0)
        chars_per_second = (effective_wpm * self.CHARACTERS_PER_WORD) / 60.0
        duration_seconds = 1.0 / max(chars_per_second, 1e-3)
        return max(1, int(duration_seconds * self._sample_rate))

    def _frequency_for_character(self, character: str, base_pitch: float) -> float:
        """Map a character to a frequency offset from the base pitch."""
        offset_semitones = ord(character) % 12
        return base_pitch * (1.0 + offset_semitones * self.SEMITONE_STEP)

    def _append_tone(
        self,
        waveform: list[float],
        frequency: float,
        sample_count: int,
        volume: float,
    ) -> None:
        """Append a sine tone of the given frequency and length to waveform."""
        angular = 2.0 * math.pi * frequency / self._sample_rate
        gain = max(0.0, min(1.0, volume))
        for n in range(sample_count):
            waveform.append(gain * math.sin(angular * n))


class TTSEngineError(RuntimeError):
    """Raised when a real TTS backend fails to render the given text.

    Callers higher up (``SoundEngine._synthesise_speech``) catch this
    and fall back to a short silence so one bad utterance never kills
    the pipeline.
    """


class Pyttsx3Engine:
    """Cross-platform TTS via the ``pyttsx3`` Python package.

    ``pyttsx3`` is an adapter that routes to SAPI5 on Windows,
    NSSpeechSynthesizer on macOS, and ``espeak-ng`` on Linux. A single
    code path therefore delivers real speech on every platform ASAT
    supports, which is what the user asked for ("consistent across
    Windows and POSIX, customisable").

    Each ``synthesize`` call:

    1. Writes the utterance to a temporary WAV file via
       ``engine.save_to_file(text, path); engine.runAndWait()``.
    2. Reads the WAV back, downmixes to mono, resamples if needed, and
       returns an ``AudioBuffer`` at ``self.sample_rate``.

    The temp file is cleaned up immediately after read so even a very
    chatty session does not litter ``/tmp``.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        *,
        voice: Optional[str] = None,
        rate: Optional[float] = None,
        volume: Optional[float] = None,
        pitch: Optional[float] = None,
    ) -> None:
        """Create an engine handle; defer backend init to first synth."""
        self._sample_rate = sample_rate
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._pitch = pitch
        self._backend: Optional[object] = None
        # Reliability: bounded ring of property rejections. Previously
        # these were silently swallowed (Fully Configurable invariant
        # was violated — a user adjusting pitch and hearing no change
        # had no way to know why). Capped at 16 entries per engine.
        self._config_rejections: list[dict[str, str]] = []

    @classmethod
    def available(cls) -> bool:
        """Return True when the ``pyttsx3`` package is importable.

        We deliberately keep this probe cheap (import only, no
        ``pyttsx3.init()``): on macOS and Windows, ``init()`` opens
        an NSSpeechSynthesizer / SAPI COM session that accumulates
        native state when probed many times in the same process (as
        happens during a pytest run). A real initialization failure
        surfaces at first ``synthesize()`` call, which the
        ``SoundEngine`` reliability guard catches — the user hears
        the fail-audible error tone instead of a silent hang, and
        ``AUDIO_PIPELINE_FAILED`` is published with details.

        For CI or environments where the default engine should not
        be pyttsx3, set ``ASAT_TTS_ENGINE=tone`` (or another engine
        id) to override priority walk; the override bypasses this
        probe entirely.
        """
        try:
            import pyttsx3  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def sample_rate(self) -> int:
        """Return the sample rate of the buffers this engine emits."""
        return self._sample_rate

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Render ``text`` through pyttsx3 and return a mono AudioBuffer."""
        if not text or text.isspace():
            return AudioBuffer.silence(0.05, self._sample_rate)
        backend = self._ensure_backend()
        self._apply_voice(backend, voice)
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            try:
                backend.save_to_file(text, str(tmp_path))
                backend.runAndWait()
            except Exception as exc:
                raise TTSEngineError(
                    f"pyttsx3 synthesis failed: {exc}"
                ) from exc
            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise TTSEngineError(
                    "pyttsx3 produced no audio; the backend may be missing a voice"
                )
            return _wav_to_mono_buffer(tmp_path.read_bytes(), self._sample_rate)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def _ensure_backend(self) -> object:
        """Import and construct the underlying pyttsx3 engine once."""
        if self._backend is not None:
            return self._backend
        try:
            import pyttsx3
        except Exception as exc:  # pragma: no cover - tested via available()
            raise TTSEngineError(
                "pyttsx3 is not installed. Run `pip install pyttsx3`."
            ) from exc
        try:
            self._backend = pyttsx3.init()
        except Exception as exc:
            raise TTSEngineError(
                f"pyttsx3 failed to initialise a backend: {exc}"
            ) from exc
        return self._backend

    def _apply_voice(self, backend: object, voice: VoiceProfile) -> None:
        """Push the VoiceProfile + user overrides into the pyttsx3 engine."""
        if self._voice is not None:
            self._try_set(backend, "voice", self._voice)
        rate = self._rate if self._rate is not None else voice.speed_wpm
        self._try_set(backend, "rate", float(rate))
        volume = self._volume if self._volume is not None else voice.volume
        self._try_set(backend, "volume", max(0.0, min(1.0, float(volume))))
        if self._pitch is not None:
            self._try_set(backend, "pitch", float(self._pitch))

    def _try_set(self, backend: object, key: str, value: object) -> None:
        """Call ``backend.setProperty(key, value)`` and record rejections.

        Some pyttsx3 drivers (notably Linux espeak) raise on unknown
        property keys. We must not let that take narration down, but
        we also must not swallow it silently: a user who configured
        pitch and doesn't hear a change deserves to know the backend
        rejected the property. Each rejection is appended to a bounded
        ring and surfaced via ``config_rejections``.
        """
        try:
            backend.setProperty(key, value)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — backend may raise anything
            record = {
                "property": str(key),
                "value": repr(value),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self._config_rejections.append(record)
            # Cap the ring at 16 so a misconfigured engine can't
            # accumulate unbounded memory over a long session.
            if len(self._config_rejections) > 16:
                self._config_rejections = self._config_rejections[-16:]

    @property
    def config_rejections(self) -> tuple[dict[str, str], ...]:
        """Return property rejections observed since engine construction.

        Surfaced here for diagnostics — callers can display these in
        ``:tts list`` output or the settings editor so the user knows
        which properties their backend silently ignored.
        """
        return tuple(self._config_rejections)


class EspeakNgEngine:
    """Direct ``espeak-ng --stdout`` subprocess adapter.

    ``espeak-ng`` is the most portable open-source speech synthesizer
    and ships in every major Linux distribution's package manager,
    plus ``brew install espeak-ng`` on macOS and downloadable Windows
    builds. Exposing it directly (rather than via ``pyttsx3``) gives
    access to the full parameter surface: ``-v`` (voice), ``-s``
    (speed), ``-p`` (pitch), ``-a`` (amplitude).
    """

    DEFAULT_VOICE = "en-us"

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        *,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        pitch: Optional[float] = None,
        amplitude: Optional[float] = None,
        binary: Optional[str] = None,
    ) -> None:
        """Remember override defaults; binary defaults to ``espeak-ng``."""
        self._sample_rate = sample_rate
        self._voice = voice or self.DEFAULT_VOICE
        self._speed = speed
        self._pitch = pitch
        self._amplitude = amplitude
        self._binary = binary or "espeak-ng"

    @classmethod
    def available(cls) -> bool:
        """Return True iff ``espeak-ng`` is on PATH."""
        return shutil.which("espeak-ng") is not None

    @property
    def sample_rate(self) -> int:
        """Return the sample rate of buffers produced by this engine."""
        return self._sample_rate

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Shell out to ``espeak-ng`` and decode its WAV stdout."""
        if not text or text.isspace():
            return AudioBuffer.silence(0.05, self._sample_rate)
        cmd = [self._binary, "--stdout", "-v", self._voice]
        speed = self._speed if self._speed is not None else voice.speed_wpm
        cmd.extend(["-s", str(int(speed))])
        if self._pitch is not None:
            cmd.extend(["-p", str(int(self._pitch))])
        amplitude = (
            self._amplitude
            if self._amplitude is not None
            else int(max(0.0, min(1.0, voice.volume)) * 200)
        )
        cmd.extend(["-a", str(int(amplitude))])
        # `--` terminates option processing so stray leading hyphens in
        # the text are not parsed as flags.
        cmd.extend(["--", text])
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=10.0,
            )
        except FileNotFoundError as exc:
            raise TTSEngineError(
                f"espeak-ng binary not found on PATH: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSEngineError("espeak-ng timed out after 10s") from exc
        except subprocess.CalledProcessError as exc:
            raise TTSEngineError(
                f"espeak-ng failed (exit {exc.returncode}): "
                f"{exc.stderr.decode(errors='replace').strip()}"
            ) from exc
        if not result.stdout:
            raise TTSEngineError("espeak-ng returned empty WAV on stdout")
        return _wav_to_mono_buffer(result.stdout, self._sample_rate)


class SystemSayEngine:
    """macOS ``say`` binary adapter.

    ``say`` ships on every macOS host and exposes the system-wide
    voice library (``say -v '?'`` to list). Emits LEI16 PCM at 22050
    Hz into a WAV wrapper so the resulting file drops into the same
    decode path as pyttsx3 and espeak-ng.
    """

    DATA_FORMAT = "LEI16@22050"

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        *,
        voice: Optional[str] = None,
        rate: Optional[float] = None,
        binary: Optional[str] = None,
    ) -> None:
        """Remember override defaults; binary defaults to ``say``."""
        self._sample_rate = sample_rate
        self._voice = voice
        self._rate = rate
        self._binary = binary or "say"

    @classmethod
    def available(cls) -> bool:
        """Return True on Darwin with ``say`` on PATH."""
        if not sys.platform.startswith("darwin"):
            return False
        return shutil.which("say") is not None

    @property
    def sample_rate(self) -> int:
        """Return the sample rate of buffers produced by this engine."""
        return self._sample_rate

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Invoke ``say`` to write a temp WAV, then decode it."""
        if not text or text.isspace():
            return AudioBuffer.silence(0.05, self._sample_rate)
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            cmd = [self._binary, "--data-format", self.DATA_FORMAT, "-o", str(tmp_path)]
            if self._voice is not None:
                cmd.extend(["-v", self._voice])
            rate = self._rate if self._rate is not None else voice.speed_wpm
            cmd.extend(["-r", str(int(rate)), "--", text])
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=10.0,
                )
            except FileNotFoundError as exc:
                raise TTSEngineError(
                    f"say binary not found on PATH: {exc}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise TTSEngineError("say timed out after 10s") from exc
            except subprocess.CalledProcessError as exc:
                raise TTSEngineError(
                    f"say failed (exit {exc.returncode}): "
                    f"{exc.stderr.decode(errors='replace').strip()}"
                ) from exc
            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise TTSEngineError("say produced no audio")
            return _wav_to_mono_buffer(tmp_path.read_bytes(), self._sample_rate)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _wav_to_mono_buffer(blob: bytes, target_sample_rate: int) -> AudioBuffer:
    """Decode a PCM16 WAV blob into a mono AudioBuffer at the target rate.

    Downmixes stereo to mono by averaging the two channels, then
    linearly resamples to ``target_sample_rate`` if the source rate
    differs. Any decode error raises ``TTSEngineError`` so callers can
    fall back gracefully.
    """
    try:
        with wave.open(io.BytesIO(blob), "rb") as reader:
            channels = reader.getnchannels()
            sample_rate = reader.getframerate()
            sample_width = reader.getsampwidth()
            frame_count = reader.getnframes()
            raw = reader.readframes(frame_count)
    except (wave.Error, EOFError) as exc:
        raise TTSEngineError(f"could not decode WAV output: {exc}") from exc
    if sample_width != 2:
        raise TTSEngineError(
            f"expected 16-bit PCM, got {sample_width * 8}-bit samples"
        )
    total_samples = len(raw) // sample_width
    ints = struct.unpack("<" + "h" * total_samples, raw)
    floats = tuple(value / 32768.0 for value in ints)
    if channels == 1:
        mono = floats
    elif channels == 2:
        mono = tuple(
            (floats[i] + floats[i + 1]) / 2.0 for i in range(0, len(floats), 2)
        )
    else:
        # N-channel is unusual in TTS output but easy to average.
        mono = tuple(
            sum(floats[i : i + channels]) / channels
            for i in range(0, len(floats), channels)
        )
    if sample_rate == target_sample_rate or sample_rate <= 0:
        return AudioBuffer.mono(mono, target_sample_rate)
    return AudioBuffer.mono(_linear_resample(mono, sample_rate, target_sample_rate), target_sample_rate)


def _linear_resample(
    samples: tuple[float, ...],
    source_rate: int,
    target_rate: int,
) -> tuple[float, ...]:
    """Linearly resample a mono sample tuple from ``source_rate`` to ``target_rate``.

    Linear interpolation is crude but dependency-free and perfectly
    adequate for speech narration, where sub-sample phase fidelity
    does not affect intelligibility. Real-time production use with
    high-quality music would want a polyphase filter; speech does not.
    """
    if not samples or source_rate == target_rate:
        return tuple(samples)
    ratio = source_rate / float(target_rate)
    out_len = max(1, int(round(len(samples) * target_rate / source_rate)))
    out: list[float] = []
    for index in range(out_len):
        position = index * ratio
        lower = int(position)
        upper = min(lower + 1, len(samples) - 1)
        frac = position - lower
        out.append(samples[lower] * (1.0 - frac) + samples[upper] * frac)
    return tuple(out)
