"""SettingsEditor: keyboard-only, self-voicing SoundBank editor.

The editor lets a user browse and mutate a `SoundBank` via a strictly
keyboard-driven state machine. Every navigation step and every
mutation emits a `SETTINGS_*` event; the SoundEngine listens for
those events so the editor narrates itself with no additional wiring.

The editor is deliberately headless. The actual integration into the
terminal's focus-mode router lives in a later phase; this module
exposes a clean API that a higher-level controller can drive from
keystrokes.

State hierarchy (three levels, walked with next / prev / enter / back):

    SECTION   ["voices", "sounds", "bindings"]
       ↓
    RECORD    list of Voice / SoundRecipe / EventBinding in that section
       ↓
    FIELD     typed fields on the current record

The user edits a field by passing a raw string to `edit(value)`. The
editor parses it against the field's declared type (float, int, bool,
optional str, mapping) and refuses the mutation on parse error
without disturbing editor state.

Every successful `edit` call produces a new immutable SoundBank via
`dataclasses.replace`, so the editor never mutates the previously-
returned bank and callers holding an older reference see no change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.sound_bank import (
    SOUND_KINDS,
    EventBinding,
    SoundBank,
    SoundBankError,
    SoundRecipe,
    Voice,
)


SOURCE = "settings_editor"


class SettingsEditorError(ValueError):
    """Raised when a navigation or edit is not legal at the current state."""


class Section(str, Enum):
    """Top-level section the editor is browsing."""

    VOICES = "voices"
    SOUNDS = "sounds"
    BINDINGS = "bindings"


class Level(str, Enum):
    """Current depth in the editor navigation stack."""

    SECTION = "section"
    RECORD = "record"
    FIELD = "field"


# Ordered field lists per record type. Using tuples keeps the editor
# itself immutable and the order shown to the user deterministic.
VOICE_FIELDS = ("id", "engine", "rate", "pitch", "volume", "azimuth", "elevation")
SOUND_FIELDS = ("id", "kind", "params", "volume", "azimuth", "elevation")
BINDING_FIELDS = (
    "id",
    "event_type",
    "voice_id",
    "sound_id",
    "say_template",
    "predicate",
    "priority",
    "enabled",
)

# Section order drives both SECTION-level navigation and how the
# editor maps a Section to the SoundBank attribute it represents.
SECTION_ORDER: tuple[Section, ...] = (Section.VOICES, Section.SOUNDS, Section.BINDINGS)


@dataclass(frozen=True)
class EditorState:
    """Snapshot of where the user is in the editor."""

    level: Level = Level.SECTION
    section: Section = Section.VOICES
    record_index: int = 0
    field_index: int = 0
    dirty: bool = False


class SettingsEditor:
    """Stateful driver for editing a SoundBank via keyboard commands.

    Construct with a starting SoundBank and an EventBus. Each method
    either moves the cursor, mutates the bank, or persists it; all
    three publish the appropriate SETTINGS_* event on the bus so the
    SoundEngine can narrate in real time.
    """

    def __init__(self, bus: EventBus, bank: SoundBank) -> None:
        """Open an editor session over the given bank."""
        self._bus = bus
        self._bank = bank
        self._state = EditorState()
        publish_event(
            bus,
            EventType.SETTINGS_OPENED,
            {"section": self._state.section.value, "record_count": self._record_count()},
            source=SOURCE,
        )
        self._publish_focus()

    @property
    def bank(self) -> SoundBank:
        """Return the current, possibly edited, SoundBank."""
        return self._bank

    @property
    def state(self) -> EditorState:
        """Return the current editor state (level + indices + dirty)."""
        return self._state

    def close(self) -> None:
        """Signal the end of an editor session."""
        publish_event(
            self._bus,
            EventType.SETTINGS_CLOSED,
            {"dirty": self._state.dirty},
            source=SOURCE,
        )

    def next(self) -> None:
        """Move one step forward at the current level (wraps at end)."""
        self._move(+1)

    def prev(self) -> None:
        """Move one step backward at the current level (wraps at start)."""
        self._move(-1)

    def enter(self) -> None:
        """Descend one level. Raises at the deepest (FIELD) level."""
        if self._state.level == Level.SECTION:
            if self._record_count() == 0:
                raise SettingsEditorError(f"section {self._state.section.value!r} is empty")
            self._state = replace(self._state, level=Level.RECORD, record_index=0, field_index=0)
        elif self._state.level == Level.RECORD:
            self._state = replace(self._state, level=Level.FIELD, field_index=0)
        else:
            raise SettingsEditorError("already at the deepest level")
        self._publish_focus()

    def back(self) -> None:
        """Ascend one level. Raises at the SECTION level."""
        if self._state.level == Level.FIELD:
            self._state = replace(self._state, level=Level.RECORD)
        elif self._state.level == Level.RECORD:
            self._state = replace(self._state, level=Level.SECTION, record_index=0, field_index=0)
        else:
            raise SettingsEditorError("already at the top level")
        self._publish_focus()

    def current_value(self) -> Any:
        """Return the value the user is currently focused on.

        At SECTION level returns the Section, at RECORD the current
        record object, at FIELD the field's current value.
        """
        if self._state.level == Level.SECTION:
            return self._state.section
        if self._state.level == Level.RECORD:
            return self._records()[self._state.record_index]
        return getattr(self._records()[self._state.record_index], self._current_field_name())

    def current_field_name(self) -> str:
        """Return the name of the field in focus; requires FIELD level."""
        if self._state.level != Level.FIELD:
            raise SettingsEditorError("no field is focused at this level")
        return self._current_field_name()

    def edit(self, raw: str) -> None:
        """Parse raw against the focused field's type and apply it.

        Raises SettingsEditorError on parse failure or when invoked at
        a level above FIELD. The underlying SoundBank is replaced in
        place; the validate() method runs after the swap so referential
        integrity is enforced on every edit.
        """
        if self._state.level != Level.FIELD:
            raise SettingsEditorError("edit is only valid at the FIELD level")
        field_name = self._current_field_name()
        parsed = self._parse_field_value(field_name, raw)
        old_record = self._records()[self._state.record_index]
        new_record = replace(old_record, **{field_name: parsed})
        new_records = list(self._records())
        new_records[self._state.record_index] = new_record
        candidate = self._bank_with_section_replaced(new_records)
        try:
            candidate.validate()
        except SoundBankError as exc:
            raise SettingsEditorError(str(exc)) from exc
        old_value = getattr(old_record, field_name)
        self._bank = candidate
        self._state = replace(self._state, dirty=True)
        publish_event(
            self._bus,
            EventType.SETTINGS_VALUE_EDITED,
            {
                "section": self._state.section.value,
                "record_index": self._state.record_index,
                "field": field_name,
                "old_value": _jsonable(old_value),
                "new_value": _jsonable(parsed),
            },
            source=SOURCE,
        )

    def save(self, path: Path | str) -> None:
        """Persist the current bank as JSON at path and clear the dirty flag."""
        self._bank.save(path)
        self._state = replace(self._state, dirty=False)
        publish_event(
            self._bus,
            EventType.SETTINGS_SAVED,
            {"path": str(path)},
            source=SOURCE,
        )

    def _move(self, delta: int) -> None:
        """Shift the cursor at the current level by delta, wrapping."""
        if self._state.level == Level.SECTION:
            index = SECTION_ORDER.index(self._state.section)
            new_section = SECTION_ORDER[(index + delta) % len(SECTION_ORDER)]
            self._state = replace(
                self._state,
                section=new_section,
                record_index=0,
                field_index=0,
            )
        elif self._state.level == Level.RECORD:
            count = self._record_count()
            if count == 0:
                raise SettingsEditorError("no records to navigate in this section")
            new_index = (self._state.record_index + delta) % count
            self._state = replace(self._state, record_index=new_index, field_index=0)
        else:
            fields = self._fields_for(self._state.section)
            new_index = (self._state.field_index + delta) % len(fields)
            self._state = replace(self._state, field_index=new_index)
        self._publish_focus()

    def _publish_focus(self) -> None:
        """Emit SETTINGS_FOCUSED describing the current cursor."""
        payload: dict[str, Any] = {
            "level": self._state.level.value,
            "section": self._state.section.value,
        }
        if self._state.level in (Level.RECORD, Level.FIELD):
            payload["record_index"] = self._state.record_index
            payload["record_id"] = getattr(
                self._records()[self._state.record_index], "id", ""
            )
        if self._state.level == Level.FIELD:
            field_name = self._current_field_name()
            payload["field"] = field_name
            payload["value"] = _jsonable(
                getattr(self._records()[self._state.record_index], field_name)
            )
        publish_event(self._bus, EventType.SETTINGS_FOCUSED, payload, source=SOURCE)

    def _records(self) -> tuple[Any, ...]:
        """Return the current section's record tuple from the bank."""
        return getattr(self._bank, self._state.section.value)

    def _record_count(self) -> int:
        """Return the number of records in the current section."""
        return len(self._records())

    def _current_field_name(self) -> str:
        """Return the name of the field the FIELD cursor is on."""
        return self._fields_for(self._state.section)[self._state.field_index]

    @staticmethod
    def _fields_for(section: Section) -> tuple[str, ...]:
        """Return the ordered field tuple for records in section."""
        if section == Section.VOICES:
            return VOICE_FIELDS
        if section == Section.SOUNDS:
            return SOUND_FIELDS
        return BINDING_FIELDS

    def _bank_with_section_replaced(self, new_records: list[Any]) -> SoundBank:
        """Return a new bank with the current section swapped for new_records."""
        section = self._state.section
        if section == Section.VOICES:
            return self._bank.with_replaced(voices=new_records)
        if section == Section.SOUNDS:
            return self._bank.with_replaced(sounds=new_records)
        return self._bank.with_replaced(bindings=new_records)

    def _parse_field_value(self, field_name: str, raw: str) -> Any:
        """Convert raw text to the declared type of field_name."""
        section = self._state.section
        if section == Section.VOICES:
            return _parse_voice_field(field_name, raw)
        if section == Section.SOUNDS:
            return _parse_sound_field(field_name, raw)
        return _parse_binding_field(field_name, raw)


def _parse_voice_field(field_name: str, raw: str) -> Any:
    """Coerce raw into the appropriate Python type for a Voice field."""
    if field_name in ("id", "engine"):
        return raw
    if field_name in ("rate", "pitch", "volume", "azimuth", "elevation"):
        return _parse_float(raw, field_name)
    raise SettingsEditorError(f"unknown voice field {field_name!r}")


def _parse_sound_field(field_name: str, raw: str) -> Any:
    """Coerce raw into the appropriate Python type for a SoundRecipe field."""
    if field_name == "id":
        return raw
    if field_name == "kind":
        if raw not in SOUND_KINDS:
            raise SettingsEditorError(f"kind must be one of {SOUND_KINDS}, got {raw!r}")
        return raw
    if field_name == "params":
        return _parse_mapping(raw, field_name)
    if field_name in ("volume", "azimuth", "elevation"):
        return _parse_float(raw, field_name)
    raise SettingsEditorError(f"unknown sound field {field_name!r}")


def _parse_binding_field(field_name: str, raw: str) -> Any:
    """Coerce raw into the appropriate Python type for an EventBinding field."""
    if field_name in ("id", "event_type", "say_template", "predicate"):
        return raw
    if field_name in ("voice_id", "sound_id"):
        stripped = raw.strip()
        if stripped.lower() in ("null", "none", ""):
            return None
        return stripped
    if field_name == "priority":
        return _parse_int(raw, field_name)
    if field_name == "enabled":
        return _parse_bool(raw, field_name)
    raise SettingsEditorError(f"unknown binding field {field_name!r}")


def _parse_float(raw: str, field_name: str) -> float:
    """Parse raw as a float or raise a field-aware error."""
    try:
        return float(raw)
    except ValueError as exc:
        raise SettingsEditorError(f"{field_name} must be a number, got {raw!r}") from exc


def _parse_int(raw: str, field_name: str) -> int:
    """Parse raw as an int or raise a field-aware error."""
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise SettingsEditorError(f"{field_name} must be an integer, got {raw!r}") from exc


def _parse_bool(raw: str, field_name: str) -> bool:
    """Parse raw as a bool; accepts true/false/yes/no/1/0 case-insensitively."""
    text = raw.strip().lower()
    if text in ("true", "yes", "1", "on"):
        return True
    if text in ("false", "no", "0", "off"):
        return False
    raise SettingsEditorError(f"{field_name} must be true or false, got {raw!r}")


def _parse_mapping(raw: str, field_name: str) -> dict[str, Any]:
    """Parse raw as a JSON object."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SettingsEditorError(f"{field_name} must be JSON, got {raw!r}") from exc
    if not isinstance(parsed, dict):
        raise SettingsEditorError(f"{field_name} must be a JSON object, got {type(parsed).__name__}")
    return parsed


def _jsonable(value: Any) -> Any:
    """Coerce a field value into something JSON-serialisable for events."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)
