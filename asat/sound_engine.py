"""SoundEngine: data-driven audio dispatcher for events.

The SoundEngine ties together the audio phases:

- SoundBank (A1) — voices, sound recipes, and event bindings as data.
- SoundGenerator (A2) — synthesises mono AudioBuffers from recipes.
- TTSEngine + Spatializer + AudioSink — the underlying playback stack.

When an event flies past on the bus, the engine looks up every binding
whose `event_type` matches, filters by the binding's `predicate`,
renders the `say_template` against the event payload, synthesises the
voice (if any) and the sound recipe (if any), spatialises both by
their azimuth / elevation, mixes the two, and hands the result to the
AudioSink. Swap in a different SoundBank and the engine starts
reacting to a different set of events the moment `set_bank(...)`
returns.

Predicates are evaluated by a pluggable `PredicateEvaluator`. The
default one supports three forms, enough for the common cases without
turning the bank into a programming language:

    <empty string>          always match
    key == <literal>        equality (literal parsed via ast.literal_eval)
    key != <literal>        inequality
    key in [<literals>]     membership in a list

Templates use `str.format_map` with a default-to-empty-string missing
lookup so a binding can reference any payload key without worrying
about key absence (the speech will simply drop the missing fragment).
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import replace
from typing import Any, Deque, NamedTuple, Optional, Protocol

from asat.audio import (
    DEFAULT_SAMPLE_RATE,
    AudioBuffer,
    ChannelLayout,
    SpatialPosition,
    VoiceProfile,
)
from asat.audio_sink import AudioSink
from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.hrtf import HRTFProfile, Spatializer
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice
from asat.sound_generators import SoundGeneratorRegistry
from asat.tts import TTSEngine, ToneTTSEngine


SOURCE_NAME = "sound_engine"

NARRATION_HISTORY_CAPACITY = 20


class NarrationHistoryEntry(NamedTuple):
    """One line of spoken narration, preserved for `repeat_last_narration`.

    `voice_id` plus `text` are the minimum to re-synthesise through the
    same TTS path; `event_type` and `binding_id` are carried for
    observers (the F39 event log viewer, future overlays) and for the
    NARRATION_REPLAYED payload.
    """

    voice_id: str
    text: str
    event_type: str
    binding_id: str


class PredicateEvaluator(Protocol):
    """Anything that decides whether a binding applies to a payload."""

    def matches(self, expression: str, payload: dict[str, Any]) -> bool:
        """Return True when the expression matches against payload."""
        ...


class DefaultPredicateEvaluator:
    """Handle the small predicate grammar described in the module docstring.

    The parser is line-level rather than fully recursive so the grammar
    stays obvious and diff-friendly. Unknown operators raise
    `SoundEngineError` at evaluation time so typos surface instead of
    silently passing.
    """

    def matches(self, expression: str, payload: dict[str, Any]) -> bool:
        """Return True iff expression matches (empty string always matches)."""
        text = expression.strip()
        if not text:
            return True
        for operator, comparer in (
            (" != ", _not_equal),
            (" == ", _equal),
            (" in ", _member_of),
        ):
            if operator in text:
                key, rhs = text.split(operator, 1)
                literal = _parse_literal(rhs.strip())
                return comparer(payload.get(key.strip()), literal)
        raise SoundEngineError(f"unsupported predicate expression: {expression!r}")


class SoundEngineError(ValueError):
    """Raised when a binding cannot be turned into audio."""


class SoundEngine:
    """Drive audio output from a SoundBank by subscribing to events."""

    def __init__(
        self,
        bus: EventBus,
        bank: SoundBank,
        sink: AudioSink,
        *,
        tts: Optional[TTSEngine] = None,
        spatializer: Optional[Spatializer] = None,
        generators: Optional[SoundGeneratorRegistry] = None,
        predicate: Optional[PredicateEvaluator] = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        history_capacity: int = NARRATION_HISTORY_CAPACITY,
    ) -> None:
        """Wire the engine to the bus and load the initial bank."""
        self._bus = bus
        self._sink = sink
        self._tts = tts or ToneTTSEngine(sample_rate=sample_rate)
        self._spatializer = spatializer or Spatializer()
        self._generators = generators or SoundGeneratorRegistry.default()
        self._predicate = predicate or DefaultPredicateEvaluator()
        self._sample_rate = sample_rate
        self._history: Deque[NarrationHistoryEntry] = deque(
            maxlen=history_capacity
        )
        self._bank: SoundBank = SoundBank()
        self._subscribed_types: set[EventType] = set()
        self.set_bank(bank)

    @property
    def bank(self) -> SoundBank:
        """Return the currently loaded SoundBank."""
        return self._bank

    @property
    def narration_history(self) -> tuple[NarrationHistoryEntry, ...]:
        """Return the ring buffer of recently-spoken phrases (oldest first)."""
        return tuple(self._history)

    def replay_last_narration(self) -> Optional[NarrationHistoryEntry]:
        """Re-speak the most recent narration through the same voice.

        Returns the replayed entry, or ``None`` when no narration has
        been spoken yet (nothing to repeat). Re-rendering bypasses the
        bank so the replay does not recursively add itself to history,
        and publishes ``NARRATION_REPLAYED`` so observers (renderer,
        tests, future audio history overlay) can trace the action.
        """
        if not self._history:
            return None
        entry = self._history[-1]
        voice = self._bank.voice_for(entry.voice_id)
        if voice is None:
            return None
        buffer = self._synthesise_speech(voice, entry.text)
        self._sink.play(buffer)
        publish_event(
            self._bus,
            EventType.NARRATION_REPLAYED,
            {
                "event_type": entry.event_type,
                "binding_id": entry.binding_id,
                "text": entry.text,
                "voice_id": entry.voice_id,
            },
            source=SOURCE_NAME,
        )
        return entry

    def set_bank(self, bank: SoundBank) -> None:
        """Replace the active bank and re-subscribe to the bus.

        Bindings pointing at `EventType` values the engine does not
        recognise are skipped; a mis-typed event_type should not take
        down the audio pipeline.
        """
        bank.validate()
        self._bank = bank
        self._resubscribe()

    def close(self) -> None:
        """Unsubscribe from every active event type and close the sink."""
        for event_type in list(self._subscribed_types):
            self._bus.unsubscribe(event_type, self._dispatch)
        self._subscribed_types.clear()
        self._sink.close()

    def _resubscribe(self) -> None:
        """Align bus subscriptions with the bank's current bindings."""
        wanted: set[EventType] = set()
        for binding in self._bank.bindings:
            if not binding.enabled:
                continue
            event_type = _event_type_from_value(binding.event_type)
            if event_type is not None:
                wanted.add(event_type)
        for event_type in self._subscribed_types - wanted:
            self._bus.unsubscribe(event_type, self._dispatch)
        for event_type in wanted - self._subscribed_types:
            self._bus.subscribe(event_type, self._dispatch)
        self._subscribed_types = wanted

    def _dispatch(self, event: Event) -> None:
        """Render every matching binding and play the result."""
        if event.source == SOURCE_NAME:
            return
        bindings = self._bank.bindings_for(event.event_type.value)
        for binding in bindings:
            if not self._predicate.matches(binding.predicate, event.payload):
                continue
            self._render(binding, event)

    def _render(self, binding: EventBinding, event: Event) -> None:
        """Render one binding into audio and route it to the sink."""
        voice = self._resolve_voice(binding)
        sound = self._resolve_sound(binding)
        text = _render_template(binding.say_template, event.payload)

        speech_buffer: Optional[AudioBuffer] = None
        if voice is not None and text:
            effective_voice = _apply_voice_overrides(voice, binding.voice_overrides)
            speech_buffer = self._synthesise_speech(effective_voice, text)

        sound_buffer: Optional[AudioBuffer] = None
        if sound is not None:
            effective_sound = _apply_sound_overrides(sound, binding.sound_overrides)
            sound_buffer = self._synthesise_sound(effective_sound)

        mixed = _mix_buffers(speech_buffer, sound_buffer, self._sample_rate)
        if mixed is None:
            return
        self._sink.play(mixed)
        if speech_buffer is not None and text and binding.voice_id:
            self._history.append(
                NarrationHistoryEntry(
                    voice_id=binding.voice_id,
                    text=text,
                    event_type=event.event_type.value,
                    binding_id=binding.id,
                )
            )
        publish_event(
            self._bus,
            EventType.AUDIO_SPOKEN,
            {
                "event_type": event.event_type.value,
                "binding_id": binding.id,
                "text": text,
                "voice_id": binding.voice_id,
                "sound_id": binding.sound_id,
            },
            source=SOURCE_NAME,
        )

    def _resolve_voice(self, binding: EventBinding) -> Optional[Voice]:
        """Return the voice this binding points at, or None."""
        return self._bank.voice_for(binding.voice_id) if binding.voice_id else None

    def _resolve_sound(self, binding: EventBinding) -> Optional[SoundRecipe]:
        """Return the sound recipe this binding points at, or None."""
        return self._bank.sound_for(binding.sound_id) if binding.sound_id else None

    def _synthesise_speech(self, voice: Voice, text: str) -> AudioBuffer:
        """Synthesise text through the TTS engine and spatialise it."""
        profile = _voice_to_profile(voice)
        mono = self._tts.synthesize(text, profile)
        return self._spatialise(mono, SpatialPosition(
            azimuth_degrees=voice.azimuth,
            elevation_degrees=voice.elevation,
        ))

    def _synthesise_sound(self, recipe: SoundRecipe) -> AudioBuffer:
        """Generate a sound recipe and spatialise it."""
        mono = self._generators.generate(recipe, sample_rate=self._sample_rate)
        return self._spatialise(mono, SpatialPosition(
            azimuth_degrees=recipe.azimuth,
            elevation_degrees=recipe.elevation,
        ))

    def _spatialise(self, mono: AudioBuffer, position: SpatialPosition) -> AudioBuffer:
        """Convolve a mono buffer with a synthetic HRTF for the position."""
        profile = HRTFProfile.synthetic(position, sample_rate=mono.sample_rate)
        return self._spatializer.spatialize(mono, profile)


def _apply_voice_overrides(voice: Voice, overrides: dict[str, float]) -> Voice:
    """Return `voice` with any per-binding field overrides applied."""
    if not overrides:
        return voice
    return replace(voice, **overrides)


def _apply_sound_overrides(
    recipe: SoundRecipe, overrides: dict[str, float]
) -> SoundRecipe:
    """Return `recipe` with any per-binding field overrides applied."""
    if not overrides:
        return recipe
    return replace(recipe, **overrides)


def _voice_to_profile(voice: Voice) -> VoiceProfile:
    """Translate a SoundBank Voice into a Phase-3 VoiceProfile.

    Rate and pitch on the Voice are multipliers around 1.0; the Phase-3
    TTSEngine wants absolute Hz and WPM, so we scale conservative
    defaults by each multiplier.
    """
    return VoiceProfile(
        name=voice.id,
        pitch_hz=140.0 * voice.pitch,
        speed_wpm=200.0 * voice.rate,
        volume=voice.volume,
        position=SpatialPosition(
            azimuth_degrees=voice.azimuth,
            elevation_degrees=voice.elevation,
        ),
    )


def _render_template(template: str, payload: dict[str, Any]) -> str:
    """Format template with payload; missing keys render as empty string."""
    if not template:
        return ""
    return template.format_map(_DefaultDict(payload))


class _DefaultDict(dict):
    """dict that returns an empty string for any missing key."""

    def __missing__(self, key: str) -> str:
        """Return empty string so templates never KeyError."""
        return ""


def _mix_buffers(
    first: Optional[AudioBuffer],
    second: Optional[AudioBuffer],
    sample_rate: int,
) -> Optional[AudioBuffer]:
    """Combine two optional stereo buffers by summing sample-for-sample.

    Returns None if both inputs are missing. The shorter buffer is
    zero-padded to the longer one's length. Output is clamped into
    [-1.0, 1.0] so two loud sources can't blow the sink's headroom.
    """
    if first is None and second is None:
        return None
    if first is None:
        return second
    if second is None:
        return first
    if first.sample_rate != second.sample_rate:
        raise SoundEngineError(
            f"cannot mix buffers with different sample rates: {first.sample_rate} vs {second.sample_rate}"
        )
    if first.layout != ChannelLayout.STEREO or second.layout != ChannelLayout.STEREO:
        raise SoundEngineError("mix expects stereo buffers")
    longer = max(len(first.samples), len(second.samples))
    left: list[float] = []
    right: list[float] = []
    for index in range(0, longer, 2):
        l_sum = _sample_or_zero(first.samples, index) + _sample_or_zero(second.samples, index)
        r_sum = _sample_or_zero(first.samples, index + 1) + _sample_or_zero(second.samples, index + 1)
        left.append(_clamp(l_sum))
        right.append(_clamp(r_sum))
    return AudioBuffer.stereo(left, right, sample_rate)


def _sample_or_zero(samples: tuple[float, ...], index: int) -> float:
    """Return samples[index] or 0.0 if the index is out of range."""
    return samples[index] if index < len(samples) else 0.0


def _clamp(value: float) -> float:
    """Clamp a single sample to [-1.0, 1.0] so mixes can't blow past unity."""
    if value > 1.0:
        return 1.0
    if value < -1.0:
        return -1.0
    return value


def _event_type_from_value(value: str) -> Optional[EventType]:
    """Return the EventType whose .value matches, or None if unknown."""
    for event_type in EventType:
        if event_type.value == value:
            return event_type
    return None


def _parse_literal(text: str) -> Any:
    """Parse a predicate RHS via ast.literal_eval with a fallback to str."""
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text


def _equal(actual: Any, expected: Any) -> bool:
    """Equality comparison used by predicates."""
    return actual == expected


def _not_equal(actual: Any, expected: Any) -> bool:
    """Inequality comparison used by predicates."""
    return actual != expected


def _member_of(actual: Any, expected: Any) -> bool:
    """Membership test: the RHS must be iterable."""
    if not isinstance(expected, (list, tuple, set, frozenset)):
        raise SoundEngineError(f"'in' predicate requires a list RHS, got {expected!r}")
    return actual in expected
