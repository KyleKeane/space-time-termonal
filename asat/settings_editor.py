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
    SOUND_OVERRIDE_FIELDS,
    VOICE_OVERRIDE_FIELDS,
    EventBinding,
    SoundBank,
    SoundBankError,
    SoundRecipe,
    Voice,
)


SOURCE = "settings_editor"


MAX_HISTORY = 64


@dataclass(frozen=True)
class _EditRecord:
    """One undoable mutation, enough to both replay and reverse."""

    section: "Section"
    record_index: int
    field: str
    old_value: Any
    new_value: Any
    bank_before: "SoundBank"
    bank_after: "SoundBank"


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
    "voice_overrides",
    "sound_overrides",
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
        self._saved_bank = bank
        self._state = EditorState()
        self._undo_stack: list[_EditRecord] = []
        self._redo_stack: list[_EditRecord] = []
        # F21b search overlay. When `_search_mode` is True the editor
        # is in the `/` composer sub-mode; keys flow into `extend_search`
        # / `backspace_search` and every mutation recomputes matches.
        # `_search_origin` remembers the cursor position the user opened
        # the overlay from so `cancel_search` can restore it verbatim.
        self._search_mode: bool = False
        self._search_buffer: str = ""
        self._search_matches: tuple[tuple[Section, int], ...] = ()
        self._search_position: int = -1
        self._search_origin: Optional[
            tuple[Level, Section, int, int]
        ] = None
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
        bank_before = self._bank
        self._bank = candidate
        self._state = replace(self._state, dirty=True)
        self._push_undo(
            _EditRecord(
                section=self._state.section,
                record_index=self._state.record_index,
                field=field_name,
                old_value=old_value,
                new_value=parsed,
                bank_before=bank_before,
                bank_after=candidate,
            )
        )
        self._redo_stack.clear()
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

    @property
    def can_undo(self) -> bool:
        """True when there is at least one edit that could be reverted."""
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        """True when at least one undone edit could be re-applied."""
        return bool(self._redo_stack)

    def undo(self) -> bool:
        """Revert the most recent edit and move it onto the redo stack.

        Returns False when there is nothing to undo. Otherwise restores
        the prior bank, refocuses the cursor on the edited field, and
        re-publishes SETTINGS_VALUE_EDITED with old/new reversed so
        subscribers (narration, logs) can react uniformly.
        """
        if not self._undo_stack:
            return False
        record = self._undo_stack.pop()
        self._bank = record.bank_before
        self._redo_stack.append(record)
        self._apply_history_cursor(record)
        self._state = replace(self._state, dirty=self._compute_dirty())
        publish_event(
            self._bus,
            EventType.SETTINGS_VALUE_EDITED,
            {
                "section": record.section.value,
                "record_index": record.record_index,
                "field": record.field,
                "old_value": _jsonable(record.new_value),
                "new_value": _jsonable(record.old_value),
            },
            source=SOURCE,
        )
        self._publish_focus()
        return True

    def redo(self) -> bool:
        """Re-apply the most recently undone edit.

        Returns False when there is nothing to redo.
        """
        if not self._redo_stack:
            return False
        record = self._redo_stack.pop()
        self._bank = record.bank_after
        self._undo_stack.append(record)
        self._apply_history_cursor(record)
        self._state = replace(self._state, dirty=self._compute_dirty())
        publish_event(
            self._bus,
            EventType.SETTINGS_VALUE_EDITED,
            {
                "section": record.section.value,
                "record_index": record.record_index,
                "field": record.field,
                "old_value": _jsonable(record.old_value),
                "new_value": _jsonable(record.new_value),
            },
            source=SOURCE,
        )
        self._publish_focus()
        return True

    def _push_undo(self, record: _EditRecord) -> None:
        """Append record to the undo stack, dropping the oldest when bounded."""
        self._undo_stack.append(record)
        if len(self._undo_stack) > MAX_HISTORY:
            # Drop from the oldest end; an older edit that rolls off
            # simply can't be undone any more, but the rest of the
            # stack still restores a coherent bank.
            del self._undo_stack[0]

    def _apply_history_cursor(self, record: _EditRecord) -> None:
        """Park the cursor on the field the history step mutated.

        Undo/redo implicitly "takes the user there" so the narration
        and any visible focus cue reflect the change they just heard.
        """
        fields = self._fields_for(record.section)
        try:
            field_index = fields.index(record.field)
        except ValueError:
            field_index = 0
        self._state = replace(
            self._state,
            level=Level.FIELD,
            section=record.section,
            record_index=record.record_index,
            field_index=field_index,
        )

    def _compute_dirty(self) -> bool:
        """True when the current bank differs from the last-saved baseline.

        Undoing the only edit since the last save should clear dirty;
        redoing past a saved point should reassert it. We compare by
        identity because every mutation (edit, undo, redo) pins
        `_bank` to one of the saved-baseline / undo-record references,
        all of which are shared across the session.
        """
        return self._bank is not self._saved_bank

    @property
    def searching(self) -> bool:
        """True while the `/` search composer is active."""
        return self._search_mode

    @property
    def search_buffer(self) -> str:
        """Return the query the user is currently composing."""
        return self._search_buffer

    @property
    def search_matches(self) -> tuple[tuple["Section", int], ...]:
        """Return the ordered `(section, record_index)` pairs that match."""
        return self._search_matches

    @property
    def search_match_count(self) -> int:
        """Return the number of matches for the current / last search."""
        return len(self._search_matches)

    def begin_search(self) -> bool:
        """Enter SEARCH sub-mode. Returns False if the bank is empty.

        The overlay is cross-section: keystrokes that follow compose a
        substring query matched against record ids (every section) plus
        `event_type` / `voice_id` / `sound_id` on bindings. On the first
        matching character the cursor parks at RECORD level inside the
        section that owns the match. `cancel_search` restores the
        cursor to its pre-search location; `commit_search` leaves the
        cursor wherever the query landed.
        """
        if self._is_bank_empty():
            return False
        if self._search_mode:
            # Second `/` while already open is a no-op; the existing
            # buffer is intentionally preserved so an accidental retap
            # doesn't wipe the query.
            return True
        self._search_mode = True
        self._search_buffer = ""
        self._search_matches = ()
        self._search_position = -1
        self._search_origin = (
            self._state.level,
            self._state.section,
            self._state.record_index,
            self._state.field_index,
        )
        publish_event(
            self._bus,
            EventType.SETTINGS_SEARCH_OPENED,
            {
                "origin_level": self._state.level.value,
                "origin_section": self._state.section.value,
                "origin_record_index": self._state.record_index,
                "origin_field_index": self._state.field_index,
            },
            source=SOURCE,
        )
        return True

    def extend_search(self, character: str) -> None:
        """Append a character to the search buffer and recompute matches."""
        if not self._search_mode:
            raise SettingsEditorError("not in search sub-mode")
        if len(character) != 1:
            raise ValueError("extend_search expects exactly one character")
        self._search_buffer += character
        self._recompute_matches(jump_to_first=True)

    def backspace_search(self) -> None:
        """Trim the last character from the search buffer.

        A backspace on an empty buffer is a silent no-op so the user
        can hammer the key without closing the overlay.
        """
        if not self._search_mode:
            raise SettingsEditorError("not in search sub-mode")
        if not self._search_buffer:
            return
        self._search_buffer = self._search_buffer[:-1]
        self._recompute_matches(jump_to_first=True)

    def commit_search(self) -> bool:
        """Close the overlay and leave the cursor on the current match.

        Preserves `_search_matches` so a later `next_search_match` /
        `prev_search_match` can keep cycling without retyping. Returns
        False when no search was active.
        """
        if not self._search_mode:
            return False
        query = self._search_buffer
        match_count = len(self._search_matches)
        self._search_mode = False
        self._search_origin = None
        publish_event(
            self._bus,
            EventType.SETTINGS_SEARCH_CLOSED,
            {
                "query": query,
                "match_count": match_count,
                "committed": True,
            },
            source=SOURCE,
        )
        return True

    def cancel_search(self) -> bool:
        """Close the overlay and restore the cursor to the pre-search spot."""
        if not self._search_mode:
            return False
        query = self._search_buffer
        origin = self._search_origin
        self._search_mode = False
        self._search_buffer = ""
        self._search_matches = ()
        self._search_position = -1
        self._search_origin = None
        if origin is not None:
            level, section, record_index, field_index = origin
            self._state = replace(
                self._state,
                level=level,
                section=section,
                record_index=record_index,
                field_index=field_index,
            )
            self._publish_focus()
        publish_event(
            self._bus,
            EventType.SETTINGS_SEARCH_CLOSED,
            {
                "query": query,
                "match_count": 0,
                "committed": False,
            },
            source=SOURCE,
        )
        return True

    def next_search_match(self) -> bool:
        """Advance the cursor to the next match; wraps at the end."""
        if not self._search_matches:
            return False
        self._search_position = (self._search_position + 1) % len(self._search_matches)
        section, record_index = self._search_matches[self._search_position]
        self._goto_record_match(section, record_index)
        return True

    def prev_search_match(self) -> bool:
        """Step the cursor to the previous match; wraps at the start."""
        if not self._search_matches:
            return False
        self._search_position = (self._search_position - 1) % len(self._search_matches)
        section, record_index = self._search_matches[self._search_position]
        self._goto_record_match(section, record_index)
        return True

    def _is_bank_empty(self) -> bool:
        """True when every section is empty — nothing to search across."""
        return not any(
            getattr(self._bank, section.value) for section in SECTION_ORDER
        )

    def _recompute_matches(self, jump_to_first: bool) -> None:
        """Rebuild the match list from the current query and bank.

        Publishes `SETTINGS_SEARCH_UPDATED` with the match count; on a
        hit, also parks the cursor at RECORD level inside the matching
        section so the existing `SETTINGS_FOCUSED` narration reads the
        record the user just landed on.
        """
        if not self._search_buffer:
            self._search_matches = ()
            self._search_position = -1
            self._publish_search_update(match=None)
            return
        query = self._search_buffer.lower()
        matches: list[tuple[Section, int]] = []
        for section in SECTION_ORDER:
            for idx, record in enumerate(getattr(self._bank, section.value)):
                if query in _search_haystack(section, record).lower():
                    matches.append((section, idx))
        self._search_matches = tuple(matches)
        if not matches:
            self._search_position = -1
            self._publish_search_update(match=None)
            return
        if jump_to_first:
            self._search_position = 0
            section, record_index = matches[0]
            self._goto_record_match(section, record_index)
        self._publish_search_update(match=matches[self._search_position])

    def _goto_record_match(self, section: Section, record_index: int) -> None:
        """Park the cursor at RECORD level on the given match."""
        self._state = replace(
            self._state,
            level=Level.RECORD,
            section=section,
            record_index=record_index,
            field_index=0,
        )
        self._publish_focus()

    def _publish_search_update(
        self, match: Optional[tuple["Section", int]]
    ) -> None:
        """Emit SETTINGS_SEARCH_UPDATED with the latest query + match."""
        payload: dict[str, Any] = {
            "query": self._search_buffer,
            "match_count": len(self._search_matches),
        }
        if match is not None:
            section, record_index = match
            payload["section"] = section.value
            payload["record_index"] = record_index
            records = getattr(self._bank, section.value)
            payload["record_id"] = getattr(records[record_index], "id", "")
        publish_event(
            self._bus,
            EventType.SETTINGS_SEARCH_UPDATED,
            payload,
            source=SOURCE,
        )

    def save(self, path: Path | str) -> None:
        """Persist the current bank as JSON at path and clear the dirty flag.

        The undo stack is preserved across saves so a user who realises
        (post-save) they want to revert an earlier edit can still do
        so. Subsequent `undo()` calls will re-mark the editor dirty
        because the bank no longer matches the freshly-saved baseline.
        """
        self._bank.save(path)
        self._saved_bank = self._bank
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
    if field_name == "voice_overrides":
        return _parse_override_mapping(raw, field_name, VOICE_OVERRIDE_FIELDS)
    if field_name == "sound_overrides":
        return _parse_override_mapping(raw, field_name, SOUND_OVERRIDE_FIELDS)
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


def _parse_override_mapping(
    raw: str, field_name: str, allowed: tuple[str, ...]
) -> dict[str, float]:
    """Parse a JSON object whose values are floats and keys are allow-listed."""
    mapping = _parse_mapping(raw, field_name)
    result: dict[str, float] = {}
    for key, value in mapping.items():
        if key not in allowed:
            raise SettingsEditorError(
                f"{field_name} has unknown field {key!r}; allowed: {allowed}"
            )
        result[key] = _parse_float(str(value), f"{field_name}.{key}")
    return result


def _search_haystack(section: Section, record: Any) -> str:
    """Return the concatenated searchable text for a record.

    Voice and SoundRecipe contribute only their `id`; EventBinding
    contributes `id`, `event_type`, and the two references
    (`voice_id` / `sound_id`, each None-safe). Joined with spaces so a
    query that accidentally spans two fields still matches.
    """
    if section == Section.BINDINGS:
        parts = (
            record.id,
            record.event_type,
            record.voice_id or "",
            record.sound_id or "",
        )
        return " ".join(part for part in parts if part)
    return record.id or ""


def _jsonable(value: Any) -> Any:
    """Coerce a field value into something JSON-serialisable for events."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)
