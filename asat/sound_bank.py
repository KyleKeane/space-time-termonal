"""SoundBank: the data model behind ASAT's parametric audio framework.

A SoundBank tells the runtime what to do when each Event flies past on
the bus. Rather than hard-coding a routing table in Python, the
mapping lives in data:

    Voice       - a parametric TTS configuration. Engine, rate, pitch,
                  volume, plus a spatial azimuth and elevation so the
                  eventual audio engine can place voices in HRTF space.
    SoundRecipe - a parametric non-speech cue. Recipes are categorised
                  by `kind` (tone, chord, sample, silence) and their
                  concrete parameters live in `params`. The generator
                  phase (A2) consumes these to synthesise audio.
    EventBinding - glue: given an EventType, optionally pick a voice
                   and a sound recipe, decorate the narration with a
                   template string, gate with a predicate, and order
                   against sibling bindings via priority.

The whole bank round-trips through JSON (see
`asat/sound_bank_schema.json` for the file shape) so an end user can
tune audio from a text editor or the upcoming in-terminal `:settings`
mode.

This module only handles the data layer: parsing, validating, looking
up. Interpreting predicates and rendering templates is the AudioEngine
(phase A3); synthesising sounds is the generator layer (phase A2).
Keeping those layers separate means the bank can be stored, diffed,
and tested without the audio stack running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


SCHEMA_VERSION = 1

SOUND_KINDS = ("tone", "chord", "sample", "silence")

VOICE_OVERRIDE_FIELDS = ("rate", "pitch", "volume", "azimuth", "elevation")
SOUND_OVERRIDE_FIELDS = ("volume", "azimuth", "elevation")

# F31: tiered narration. A binding's `verbosity` says how chatty it is
# ("minimal" = critical only, "verbose" = chatty), and the bank's
# `verbosity_level` is the user's current ceiling. The engine plays a
# binding only when its rank <= the bank's rank, so raising the level
# unlocks chattier tiers and lowering it silences them without losing
# the bindings themselves.
VERBOSITY_LEVELS: tuple[str, ...] = ("minimal", "normal", "verbose")
VERBOSITY_RANK: dict[str, int] = {level: rank for rank, level in enumerate(VERBOSITY_LEVELS)}


class SoundBankError(ValueError):
    """Raised when a SoundBank document fails structural validation."""


@dataclass(frozen=True)
class Voice:
    """Parametric TTS configuration for a single narrator voice.

    `id` is the stable handle other records reference. `engine` names
    the TTS backend (the Phase 3 stub engine, a future Windows SAPI
    adapter, etc.); the empty string means "the default engine".

    Rate / pitch / volume are multipliers around 1.0 so a user can
    nudge a voice brighter or calmer without knowing absolute units.

    Azimuth and elevation are degrees in the listener-centred HRTF
    frame: azimuth ranges -180..180 (0 = straight ahead, +90 = right
    ear), elevation ranges -90..90 (0 = eye level, +90 = overhead).
    """

    id: str
    engine: str = ""
    rate: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0
    azimuth: float = 0.0
    elevation: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this voice to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "engine": self.engine,
            "rate": self.rate,
            "pitch": self.pitch,
            "volume": self.volume,
            "azimuth": self.azimuth,
            "elevation": self.elevation,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Voice":
        """Rebuild a Voice from a previously serialized mapping."""
        _require(isinstance(data, Mapping), "voice entry must be a mapping")
        _require("id" in data and isinstance(data["id"], str), "voice.id is required")
        return cls(
            id=data["id"],
            engine=str(data.get("engine", "")),
            rate=_positive_float(data.get("rate", 1.0), "voice.rate"),
            pitch=_positive_float(data.get("pitch", 1.0), "voice.pitch"),
            volume=_non_negative_float(data.get("volume", 1.0), "voice.volume"),
            azimuth=_angle(data.get("azimuth", 0.0), "voice.azimuth", -180.0, 180.0),
            elevation=_angle(data.get("elevation", 0.0), "voice.elevation", -90.0, 90.0),
            metadata=dict(data.get("metadata", {}) or {}),
        )


@dataclass(frozen=True)
class SoundRecipe:
    """Parametric non-speech cue.

    `kind` drives interpretation. Phase A2 will ship generators for
    each supported kind; unknown kinds fail validation to keep JSON
    files honest.

    Common supported kinds:

    tone
        params: frequency (Hz), duration (s), waveform (str), attack,
        release (s), harmonics (list of partial multipliers).
    chord
        params: frequencies (list[Hz]), duration, waveform, spread.
    sample
        params: path (str), loop (bool), start (s), end (s).
    silence
        params: duration (s). Useful for explicit gaps.

    All recipes share `volume`, `azimuth`, `elevation` so the spatial
    framework can place cues the same way it places voices.
    """

    id: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    volume: float = 1.0
    azimuth: float = 0.0
    elevation: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize this recipe to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "kind": self.kind,
            "params": dict(self.params),
            "volume": self.volume,
            "azimuth": self.azimuth,
            "elevation": self.elevation,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SoundRecipe":
        """Rebuild a SoundRecipe from a previously serialized mapping."""
        _require(isinstance(data, Mapping), "sound entry must be a mapping")
        _require("id" in data and isinstance(data["id"], str), "sound.id is required")
        kind = str(data.get("kind", ""))
        _require(
            kind in SOUND_KINDS,
            f"sound.kind must be one of {SOUND_KINDS}, got {kind!r}",
        )
        params = data.get("params", {}) or {}
        _require(isinstance(params, Mapping), "sound.params must be a mapping")
        return cls(
            id=data["id"],
            kind=kind,
            params=dict(params),
            volume=_non_negative_float(data.get("volume", 1.0), "sound.volume"),
            azimuth=_angle(data.get("azimuth", 0.0), "sound.azimuth", -180.0, 180.0),
            elevation=_angle(data.get("elevation", 0.0), "sound.elevation", -90.0, 90.0),
        )


@dataclass(frozen=True)
class EventBinding:
    """Route one EventType to an optional voice, sound, and template.

    `event_type` matches an `EventType.value` string (e.g. "cell.created").
    `voice_id` / `sound_id` reference records in the same bank; at least
    one of them must be set so the binding has some audible effect.

    `say_template` is a Python `str.format_map`-style template rendered
    against the event payload. Missing keys render as empty string
    (handled by the engine in phase A3).

    `predicate` is an opaque expression string the AudioEngine will
    parse in A3. For A1 it is stored verbatim so the editor can round
    trip it through JSON without loss.

    `priority` orders sibling bindings when multiple match the same
    event: higher priority runs first. `enabled=False` keeps a binding
    in the bank but silences it without losing its parameters.

    `voice_overrides` and `sound_overrides` are optional per-binding
    parameter tweaks. They let the same voice or sound record be
    re-used across many events with small variations (a lower pitch
    for errors, a higher azimuth for remote-machine output, a quieter
    volume for chatty debug chunks) without having to clone the
    underlying record. Unknown keys raise at load time so typos surface
    instead of silently being ignored.
    """

    id: str
    event_type: str
    voice_id: Optional[str] = None
    sound_id: Optional[str] = None
    say_template: str = ""
    predicate: str = ""
    priority: int = 100
    enabled: bool = True
    voice_overrides: dict[str, float] = field(default_factory=dict)
    sound_overrides: dict[str, float] = field(default_factory=dict)
    # F31: which verbosity tier this binding belongs to. The engine
    # filters out bindings whose tier is stricter than the bank's
    # current `verbosity_level` (e.g. a "verbose" keystroke cue is
    # silent under "minimal"), but never modifies the binding itself
    # so flipping back restores the cue without an edit.
    verbosity: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        """Serialize this binding to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "voice_id": self.voice_id,
            "sound_id": self.sound_id,
            "say_template": self.say_template,
            "predicate": self.predicate,
            "priority": self.priority,
            "enabled": self.enabled,
            "voice_overrides": dict(self.voice_overrides),
            "sound_overrides": dict(self.sound_overrides),
            "verbosity": self.verbosity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventBinding":
        """Rebuild an EventBinding from a previously serialized mapping."""
        _require(isinstance(data, Mapping), "binding entry must be a mapping")
        _require("id" in data and isinstance(data["id"], str), "binding.id is required")
        _require(
            "event_type" in data and isinstance(data["event_type"], str),
            "binding.event_type is required",
        )
        voice_id = data.get("voice_id")
        sound_id = data.get("sound_id")
        say_template = str(data.get("say_template", ""))
        _require(
            voice_id or sound_id or say_template,
            f"binding {data['id']!r} must set voice_id, sound_id, or say_template",
        )
        voice_overrides = _parse_overrides(
            data.get("voice_overrides", {}),
            VOICE_OVERRIDE_FIELDS,
            "binding.voice_overrides",
        )
        sound_overrides = _parse_overrides(
            data.get("sound_overrides", {}),
            SOUND_OVERRIDE_FIELDS,
            "binding.sound_overrides",
        )
        return cls(
            id=data["id"],
            event_type=data["event_type"],
            voice_id=_optional_str(voice_id, "binding.voice_id"),
            sound_id=_optional_str(sound_id, "binding.sound_id"),
            say_template=say_template,
            predicate=str(data.get("predicate", "")),
            priority=int(data.get("priority", 100)),
            enabled=bool(data.get("enabled", True)),
            voice_overrides=voice_overrides,
            sound_overrides=sound_overrides,
            verbosity=_verbosity(data.get("verbosity", "normal"), "binding.verbosity"),
        )


@dataclass(frozen=True)
class SoundBank:
    """Complete audio configuration: voices + sounds + bindings.

    A SoundBank is immutable. To edit one, build a new instance via
    `replace_binding`, `with_voice`, etc. (added in A5) or mutate
    through the upcoming settings editor which serialises via JSON.
    """

    voices: tuple[Voice, ...] = ()
    sounds: tuple[SoundRecipe, ...] = ()
    bindings: tuple[EventBinding, ...] = ()
    version: int = SCHEMA_VERSION
    # F32: when `ducking_enabled` is True, the engine attenuates a
    # concurrent non-speech buffer to `duck_level * gain` whenever a
    # speech buffer is in the same mix cycle, so narration stays
    # intelligible over per-event cues. Both fields are scalar so
    # they round-trip through JSON without any custom coercion.
    ducking_enabled: bool = True
    duck_level: float = 0.4
    # F31: bank-wide verbosity ceiling. The engine plays a binding
    # only when its `verbosity` rank is <= this level's rank
    # (minimal=0 < normal=1 < verbose=2). Lowering the ceiling
    # silences chattier tiers without removing the bindings.
    verbosity_level: str = "normal"

    def voice_for(self, voice_id: str) -> Optional[Voice]:
        """Return the voice with this id, or None if it is missing."""
        for voice in self.voices:
            if voice.id == voice_id:
                return voice
        return None

    def sound_for(self, sound_id: str) -> Optional[SoundRecipe]:
        """Return the sound recipe with this id, or None if absent."""
        for sound in self.sounds:
            if sound.id == sound_id:
                return sound
        return None

    def bindings_for(
        self,
        event_type: str,
        *,
        include_disabled: bool = False,
        respect_verbosity: bool = True,
    ) -> tuple[EventBinding, ...]:
        """Return bindings matching event_type, sorted by priority desc.

        Enabled bindings come first by default; pass include_disabled
        when an editor needs to surface the full list. Verbosity
        filtering (F31) is on by default so the engine sees only the
        bindings the current preset allows; the editor passes
        ``respect_verbosity=False`` to surface all bindings.
        """
        ceiling = VERBOSITY_RANK[self.verbosity_level]
        matches = [
            binding
            for binding in self.bindings
            if binding.event_type == event_type
            and (include_disabled or binding.enabled)
            and (
                not respect_verbosity
                or VERBOSITY_RANK.get(binding.verbosity, VERBOSITY_RANK["normal"]) <= ceiling
            )
        ]
        matches.sort(key=lambda b: b.priority, reverse=True)
        return tuple(matches)

    def with_verbosity_level(self, level: str) -> "SoundBank":
        """Return a copy of the bank with `verbosity_level` set to `level`.

        Raises `SoundBankError` for unknown levels so callers (engine,
        meta-command handler) get a uniform validation surface.
        """
        return replace(self, verbosity_level=_verbosity(level, "verbosity_level"))

    def validate(self) -> None:
        """Raise SoundBankError if internal references are inconsistent.

        The JSON loader performs per-field validation; this method
        catches cross-record issues the loader cannot: duplicate ids,
        bindings that reference a missing voice or sound.
        """
        _unique_ids(self.voices, "voice")
        _unique_ids(self.sounds, "sound")
        _unique_ids(self.bindings, "binding")
        voice_ids = {voice.id for voice in self.voices}
        sound_ids = {sound.id for sound in self.sounds}
        for binding in self.bindings:
            if binding.voice_id is not None and binding.voice_id not in voice_ids:
                raise SoundBankError(
                    f"binding {binding.id!r} references unknown voice_id {binding.voice_id!r}"
                )
            if binding.sound_id is not None and binding.sound_id not in sound_ids:
                raise SoundBankError(
                    f"binding {binding.id!r} references unknown sound_id {binding.sound_id!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the entire bank to a JSON-compatible dictionary."""
        return {
            "version": self.version,
            "voices": [voice.to_dict() for voice in self.voices],
            "sounds": [sound.to_dict() for sound in self.sounds],
            "bindings": [binding.to_dict() for binding in self.bindings],
            "ducking_enabled": self.ducking_enabled,
            "duck_level": self.duck_level,
            "verbosity_level": self.verbosity_level,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SoundBank":
        """Rebuild a SoundBank from a previously serialized mapping."""
        _require(isinstance(data, Mapping), "sound bank must be a mapping")
        version = int(data.get("version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            raise SoundBankError(
                f"unsupported sound bank version {version} (expected {SCHEMA_VERSION})"
            )
        voices = tuple(Voice.from_dict(item) for item in data.get("voices", []) or ())
        sounds = tuple(SoundRecipe.from_dict(item) for item in data.get("sounds", []) or ())
        bindings = tuple(
            EventBinding.from_dict(item) for item in data.get("bindings", []) or ()
        )
        ducking_enabled = bool(data.get("ducking_enabled", True))
        duck_level = _unit_float(data.get("duck_level", 0.4), "duck_level")
        verbosity_level = _verbosity(data.get("verbosity_level", "normal"), "verbosity_level")
        bank = cls(
            voices=voices,
            sounds=sounds,
            bindings=bindings,
            version=version,
            ducking_enabled=ducking_enabled,
            duck_level=duck_level,
            verbosity_level=verbosity_level,
        )
        bank.validate()
        return bank

    @classmethod
    def load(cls, path: Path | str) -> "SoundBank":
        """Read a SoundBank from a JSON file previously written by save."""
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SoundBankError(f"{source} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def save(self, path: Path | str) -> None:
        """Write the bank as pretty-printed JSON to the given path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def with_replaced(
        self,
        *,
        voices: Optional[Iterable[Voice]] = None,
        sounds: Optional[Iterable[SoundRecipe]] = None,
        bindings: Optional[Iterable[EventBinding]] = None,
    ) -> "SoundBank":
        """Return a copy with any of the three lists replaced wholesale.

        Kept tight on purpose: the full editor (A5) will provide
        finer-grained helpers, but tests and migrations often want a
        blunt "swap this section" operation.
        """
        return replace(
            self,
            voices=tuple(voices) if voices is not None else self.voices,
            sounds=tuple(sounds) if sounds is not None else self.sounds,
            bindings=tuple(bindings) if bindings is not None else self.bindings,
        )


def _require(condition: bool, message: str) -> None:
    """Raise SoundBankError if condition is false."""
    if not condition:
        raise SoundBankError(message)


def _positive_float(value: Any, label: str) -> float:
    """Parse value as a float > 0 or raise SoundBankError."""
    number = _as_float(value, label)
    if number <= 0:
        raise SoundBankError(f"{label} must be > 0, got {number}")
    return number


def _non_negative_float(value: Any, label: str) -> float:
    """Parse value as a float >= 0 or raise SoundBankError."""
    number = _as_float(value, label)
    if number < 0:
        raise SoundBankError(f"{label} must be >= 0, got {number}")
    return number


def _angle(value: Any, label: str, low: float, high: float) -> float:
    """Parse value as a float clamped to [low, high]."""
    number = _as_float(value, label)
    if number < low or number > high:
        raise SoundBankError(f"{label} must be in [{low}, {high}], got {number}")
    return number


def _verbosity(value: Any, label: str) -> str:
    """Coerce value to a known verbosity level; raise on anything else."""
    if not isinstance(value, str):
        raise SoundBankError(f"{label} must be a string, got {type(value).__name__}")
    if value not in VERBOSITY_RANK:
        raise SoundBankError(
            f"{label} must be one of {VERBOSITY_LEVELS}, got {value!r}"
        )
    return value


def _unit_float(value: Any, label: str) -> float:
    """Parse value as a float in [0.0, 1.0]; rejects out-of-range numbers."""
    number = _as_float(value, label)
    if number < 0.0 or number > 1.0:
        raise SoundBankError(f"{label} must be in [0.0, 1.0], got {number}")
    return number


def _as_float(value: Any, label: str) -> float:
    """Best-effort float coercion with a clear error message."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SoundBankError(f"{label} must be a number, got {value!r}") from exc


def _optional_str(value: Any, label: str) -> Optional[str]:
    """Coerce None-or-str cleanly, rejecting other types."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    raise SoundBankError(f"{label} must be a string or null, got {type(value).__name__}")


def _parse_overrides(
    value: Any,
    allowed: tuple[str, ...],
    label: str,
) -> dict[str, float]:
    """Parse an override mapping, restricting keys to the allowed set."""
    if value is None:
        return {}
    _require(isinstance(value, Mapping), f"{label} must be a mapping")
    parsed: dict[str, float] = {}
    for key, raw in value.items():
        if key not in allowed:
            raise SoundBankError(
                f"{label} has unknown field {key!r}; allowed: {allowed}"
            )
        parsed[key] = _as_float(raw, f"{label}.{key}")
    return parsed


def _unique_ids(items: Iterable[Any], kind: str) -> None:
    """Raise if any two items share the same `.id`."""
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise SoundBankError(f"duplicate {kind} id: {item.id!r}")
        seen.add(item.id)
