"""Unit tests for the keyboard-driven SettingsEditor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asat.default_bank import default_sound_bank
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.settings_editor import (
    BINDING_FIELDS,
    Level,
    SECTION_ORDER,
    SOUND_FIELDS,
    Section,
    SettingsEditor,
    SettingsEditorError,
    VOICE_FIELDS,
)
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice


def _bank() -> SoundBank:
    """A tiny multi-section bank used across the tests."""
    return SoundBank(
        voices=(Voice(id="v1", rate=1.0, pitch=1.0), Voice(id="v2", rate=1.1)),
        sounds=(
            SoundRecipe(id="s1", kind="tone", params={"frequency": 440.0}),
            SoundRecipe(id="s2", kind="silence", params={"duration": 0.1}),
        ),
        bindings=(
            EventBinding(
                id="b1",
                event_type="cell.created",
                voice_id="v1",
                say_template="hello",
            ),
        ),
    )


class _Recorder:
    """Collect every event published on the bus so tests can inspect them."""

    def __init__(self, bus: EventBus, event_type: EventType) -> None:
        self.events: list[Event] = []
        bus.subscribe(event_type, self.events.append)


class OpenCloseTests(unittest.TestCase):

    def test_opening_publishes_settings_opened(self) -> None:
        bus = EventBus()
        opened = _Recorder(bus, EventType.SETTINGS_OPENED)
        editor = SettingsEditor(bus, _bank())
        self.assertEqual(len(opened.events), 1)
        self.assertEqual(editor.state.level, Level.SECTION)
        self.assertEqual(editor.state.section, Section.VOICES)

    def test_closing_publishes_settings_closed_with_dirty_flag(self) -> None:
        bus = EventBus()
        closed = _Recorder(bus, EventType.SETTINGS_CLOSED)
        editor = SettingsEditor(bus, _bank())
        editor.close()
        self.assertEqual(len(closed.events), 1)
        self.assertFalse(closed.events[0].payload["dirty"])


class NavigationTests(unittest.TestCase):

    def test_section_navigation_wraps_forward(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.next()
        self.assertEqual(editor.state.section, Section.SOUNDS)
        editor.next()
        self.assertEqual(editor.state.section, Section.BINDINGS)
        editor.next()
        self.assertEqual(editor.state.section, Section.VOICES)

    def test_section_navigation_wraps_backward(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.prev()
        self.assertEqual(editor.state.section, SECTION_ORDER[-1])

    def test_enter_record_then_field(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        self.assertEqual(editor.state.level, Level.RECORD)
        editor.enter()
        self.assertEqual(editor.state.level, Level.FIELD)
        self.assertEqual(editor.current_field_name(), VOICE_FIELDS[0])

    def test_back_ascends_from_field_to_section(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        editor.enter()
        editor.back()
        self.assertEqual(editor.state.level, Level.RECORD)
        editor.back()
        self.assertEqual(editor.state.level, Level.SECTION)

    def test_back_at_top_level_raises(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        with self.assertRaises(SettingsEditorError):
            editor.back()

    def test_enter_at_field_level_raises(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        editor.enter()
        with self.assertRaises(SettingsEditorError):
            editor.enter()

    def test_record_wraps_around(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        editor.next()
        self.assertEqual(editor.state.record_index, 1)
        editor.next()
        self.assertEqual(editor.state.record_index, 0)

    def test_field_wraps_around(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        editor.enter()
        for _ in range(len(VOICE_FIELDS)):
            editor.next()
        self.assertEqual(editor.state.field_index, 0)

    def test_enter_empty_section_raises(self) -> None:
        empty = SoundBank()
        editor = SettingsEditor(EventBus(), empty)
        with self.assertRaises(SettingsEditorError):
            editor.enter()

    def test_every_navigation_step_publishes_focused_event(self) -> None:
        bus = EventBus()
        focused = _Recorder(bus, EventType.SETTINGS_FOCUSED)
        editor = SettingsEditor(bus, _bank())
        initial_count = len(focused.events)
        editor.next()
        editor.enter()
        editor.next()
        editor.enter()
        self.assertGreater(len(focused.events), initial_count)


class CurrentValueTests(unittest.TestCase):

    def test_section_level_returns_section_enum(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        self.assertEqual(editor.current_value(), Section.VOICES)

    def test_record_level_returns_record(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        value = editor.current_value()
        self.assertIsInstance(value, Voice)

    def test_field_level_returns_field_value(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        editor.enter()
        self.assertEqual(editor.current_value(), "v1")

    def test_current_field_name_raises_above_field_level(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        with self.assertRaises(SettingsEditorError):
            editor.current_field_name()


class EditTests(unittest.TestCase):

    def _field_editor(self, section: Section, record_index: int, field_name: str) -> SettingsEditor:
        """Build an editor parked on a specific FIELD cursor."""
        editor = SettingsEditor(EventBus(), _bank())
        for _ in range(SECTION_ORDER.index(section)):
            editor.next()
        editor.enter()
        for _ in range(record_index):
            editor.next()
        editor.enter()
        fields = (
            VOICE_FIELDS
            if section == Section.VOICES
            else SOUND_FIELDS
            if section == Section.SOUNDS
            else BINDING_FIELDS
        )
        for _ in range(fields.index(field_name)):
            editor.next()
        return editor

    def test_edit_voice_rate_updates_bank(self) -> None:
        editor = self._field_editor(Section.VOICES, 0, "rate")
        editor.edit("1.3")
        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.3)
        self.assertTrue(editor.state.dirty)

    def test_edit_sound_params_accepts_json(self) -> None:
        editor = self._field_editor(Section.SOUNDS, 0, "params")
        editor.edit('{"frequency": 660, "duration": 0.2}')
        self.assertEqual(
            editor.bank.sounds[0].params,
            {"frequency": 660, "duration": 0.2},
        )

    def test_edit_binding_enabled_accepts_booleans(self) -> None:
        editor = self._field_editor(Section.BINDINGS, 0, "enabled")
        editor.edit("false")
        self.assertFalse(editor.bank.bindings[0].enabled)

    def test_edit_binding_voice_id_null_clears_reference(self) -> None:
        editor = self._field_editor(Section.BINDINGS, 0, "voice_id")
        editor.edit("null")
        self.assertIsNone(editor.bank.bindings[0].voice_id)

    def test_edit_rejects_non_numeric_for_numeric_field(self) -> None:
        editor = self._field_editor(Section.VOICES, 0, "rate")
        with self.assertRaises(SettingsEditorError):
            editor.edit("fast")

    def test_edit_rejects_invalid_json_params(self) -> None:
        editor = self._field_editor(Section.SOUNDS, 0, "params")
        with self.assertRaises(SettingsEditorError):
            editor.edit("not json")

    def test_edit_rejects_unknown_sound_kind(self) -> None:
        editor = self._field_editor(Section.SOUNDS, 0, "kind")
        with self.assertRaises(SettingsEditorError):
            editor.edit("trumpet")

    def test_edit_refuses_above_field_level(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        with self.assertRaises(SettingsEditorError):
            editor.edit("anything")

    def test_edit_that_breaks_reference_integrity_is_refused(self) -> None:
        editor = self._field_editor(Section.VOICES, 0, "id")
        with self.assertRaises(SettingsEditorError):
            editor.edit("renamed")
        # The bank must remain unchanged on a rejected edit.
        self.assertEqual(editor.bank.voices[0].id, "v1")
        self.assertFalse(editor.state.dirty)

    def test_edit_publishes_value_edited_event(self) -> None:
        bus = EventBus()
        edited: list[Event] = []
        bus.subscribe(EventType.SETTINGS_VALUE_EDITED, edited.append)
        editor = SettingsEditor(bus, _bank())
        editor.enter()
        editor.enter()
        editor.next()  # move to "engine" field
        editor.edit("sapi")
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0].payload["field"], "engine")
        self.assertEqual(edited[0].payload["new_value"], "sapi")


class UndoRedoTests(unittest.TestCase):

    def _rate_editor(self) -> SettingsEditor:
        editor = SettingsEditor(EventBus(), _bank())
        editor.enter()
        editor.enter()
        # move to "rate" field
        for _ in range(VOICE_FIELDS.index("rate")):
            editor.next()
        return editor

    def test_fresh_editor_has_no_history(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        self.assertFalse(editor.can_undo)
        self.assertFalse(editor.can_redo)

    def test_undo_reverts_the_most_recent_edit(self) -> None:
        editor = self._rate_editor()
        editor.edit("1.3")
        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.3)
        self.assertTrue(editor.can_undo)
        self.assertFalse(editor.can_redo)

        self.assertTrue(editor.undo())

        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.0)
        self.assertFalse(editor.can_undo)
        self.assertTrue(editor.can_redo)

    def test_redo_reapplies_the_undone_edit(self) -> None:
        editor = self._rate_editor()
        editor.edit("1.3")
        editor.undo()

        self.assertTrue(editor.redo())

        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.3)
        self.assertTrue(editor.can_undo)
        self.assertFalse(editor.can_redo)

    def test_undo_empty_stack_returns_false(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        self.assertFalse(editor.undo())

    def test_redo_empty_stack_returns_false(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        self.assertFalse(editor.redo())

    def test_new_edit_clears_the_redo_stack(self) -> None:
        editor = self._rate_editor()
        editor.edit("1.3")
        editor.undo()
        self.assertTrue(editor.can_redo)

        editor.edit("1.7")

        self.assertFalse(editor.can_redo)
        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.7)

    def test_multiple_edits_unwind_in_reverse_order(self) -> None:
        editor = self._rate_editor()
        editor.edit("1.3")
        editor.edit("1.5")
        editor.edit("1.7")

        editor.undo()
        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.5)
        editor.undo()
        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.3)
        editor.undo()
        self.assertAlmostEqual(editor.bank.voices[0].rate, 1.0)
        self.assertFalse(editor.can_undo)

    def test_undo_restores_bank_identity_so_dirty_flag_clears(self) -> None:
        editor = self._rate_editor()
        editor.edit("1.3")
        self.assertTrue(editor.state.dirty)

        editor.undo()

        self.assertFalse(editor.state.dirty)

    def test_undo_past_save_marks_editor_dirty_again(self) -> None:
        editor = self._rate_editor()
        editor.edit("1.3")
        with tempfile.TemporaryDirectory() as tmp:
            editor.save(Path(tmp) / "bank.json")
            self.assertFalse(editor.state.dirty)

            editor.undo()

            self.assertTrue(editor.state.dirty)

    def test_undo_publishes_value_edited_with_reversed_values(self) -> None:
        bus = EventBus()
        edited: list[Event] = []
        bus.subscribe(EventType.SETTINGS_VALUE_EDITED, edited.append)
        editor = SettingsEditor(bus, _bank())
        editor.enter()
        editor.enter()
        for _ in range(VOICE_FIELDS.index("rate")):
            editor.next()
        editor.edit("1.3")
        edited.clear()

        editor.undo()

        self.assertEqual(len(edited), 1)
        self.assertAlmostEqual(edited[0].payload["old_value"], 1.3)
        self.assertAlmostEqual(edited[0].payload["new_value"], 1.0)

    def test_undo_parks_cursor_on_the_mutated_field(self) -> None:
        editor = SettingsEditor(EventBus(), _bank())
        # Edit voices[0].rate via the helper, then navigate away entirely.
        editor.enter()
        editor.enter()
        for _ in range(VOICE_FIELDS.index("rate")):
            editor.next()
        editor.edit("1.3")
        editor.back()
        editor.back()
        editor.next()  # Section → sounds

        editor.undo()

        self.assertEqual(editor.state.level, Level.FIELD)
        self.assertEqual(editor.state.section, Section.VOICES)
        self.assertEqual(editor.state.record_index, 0)
        self.assertEqual(editor.current_field_name(), "rate")

    def test_history_is_bounded(self) -> None:
        # The bounded stack drops the oldest record; editing more than
        # MAX_HISTORY times should leave exactly MAX_HISTORY undos
        # available rather than growing without limit.
        from asat.settings_editor import MAX_HISTORY

        editor = self._rate_editor()
        for i in range(MAX_HISTORY + 5):
            editor.edit(f"{1.0 + 0.01 * (i + 1):.3f}")

        undone = 0
        while editor.undo():
            undone += 1
        self.assertEqual(undone, MAX_HISTORY)


class SearchOverlayTests(unittest.TestCase):
    """F21b: `/` search sub-mode over all three sections."""

    def _search_bank(self) -> SoundBank:
        """A multi-section bank with distinguishable ids for search asserts."""
        return SoundBank(
            voices=(
                Voice(id="narrator", rate=1.0),
                Voice(id="alert", rate=1.0),
            ),
            sounds=(
                SoundRecipe(id="tick", kind="tone", params={"frequency": 880.0}),
                SoundRecipe(id="chord", kind="chord", params={"frequencies": [440.0, 660.0]}),
            ),
            bindings=(
                EventBinding(
                    id="session_opened",
                    event_type="session.created",
                    voice_id="narrator",
                    say_template="hi",
                ),
                EventBinding(
                    id="submit_cue",
                    event_type="command.submitted",
                    voice_id="alert",
                    sound_id="tick",
                ),
            ),
        )

    def test_begin_search_enters_sub_mode(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        self.assertFalse(editor.searching)
        self.assertTrue(editor.begin_search())
        self.assertTrue(editor.searching)
        self.assertEqual(editor.search_buffer, "")

    def test_begin_search_empty_bank_returns_false(self) -> None:
        editor = SettingsEditor(EventBus(), SoundBank())
        self.assertFalse(editor.begin_search())
        self.assertFalse(editor.searching)

    def test_begin_search_publishes_opened_event_with_origin(self) -> None:
        bus = EventBus()
        opened = _Recorder(bus, EventType.SETTINGS_SEARCH_OPENED)
        editor = SettingsEditor(bus, self._search_bank())
        editor.enter()  # descend to RECORD so origin is non-default
        editor.begin_search()
        self.assertEqual(len(opened.events), 1)
        payload = opened.events[0].payload
        self.assertEqual(payload["origin_level"], "record")
        self.assertEqual(payload["origin_section"], "voices")

    def test_second_begin_while_open_preserves_buffer(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.extend_search("n")
        editor.extend_search("a")
        editor.begin_search()  # accidental retap
        self.assertEqual(editor.search_buffer, "na")

    def test_extend_jumps_to_first_matching_record(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.extend_search("n")  # matches "narrator"
        self.assertEqual(editor.state.level, Level.RECORD)
        self.assertEqual(editor.state.section, Section.VOICES)
        self.assertEqual(editor.state.record_index, 0)

    def test_extend_crosses_section_boundaries(self) -> None:
        """A query that only matches in SOUNDS or BINDINGS must jump there
        regardless of the section the user was parked on."""
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.extend_search("c")  # "chord" in sounds, plus "tick" / "session_opened" partial? No — just chord
        editor.extend_search("h")  # narrows
        editor.extend_search("o")
        editor.extend_search("r")
        editor.extend_search("d")
        self.assertEqual(editor.state.section, Section.SOUNDS)
        self.assertEqual(editor.state.record_index, 1)

    def test_extend_matches_binding_event_type(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        for ch in "command.":
            editor.extend_search(ch)
        self.assertEqual(editor.state.section, Section.BINDINGS)
        self.assertEqual(editor.state.record_index, 1)

    def test_extend_matches_binding_voice_id(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        for ch in "alert":
            editor.extend_search(ch)
        # "alert" matches voices[1].id first in SECTION_ORDER, but that's
        # expected: cross-section order is voices → sounds → bindings.
        self.assertEqual(editor.state.section, Section.VOICES)
        self.assertEqual(editor.state.record_index, 1)
        # Every match is still tracked, including bindings[1] whose
        # voice_id = "alert".
        matches = editor.search_matches
        self.assertIn((Section.VOICES, 1), matches)
        self.assertIn((Section.BINDINGS, 1), matches)

    def test_extend_matches_are_case_insensitive(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        for ch in "NAR":
            editor.extend_search(ch)
        self.assertEqual(editor.state.record_index, 0)

    def test_extend_no_match_leaves_cursor_in_place(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.enter()
        editor.next()  # voices[1] = alert
        editor.begin_search()
        editor.extend_search("z")  # no match anywhere
        self.assertEqual(editor.search_match_count, 0)
        # Cursor stays wherever it was — composer silently reports "no match".
        self.assertEqual(editor.state.section, Section.VOICES)
        self.assertEqual(editor.state.record_index, 1)

    def test_backspace_recomputes_matches(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        for ch in "chord":
            editor.extend_search(ch)
        self.assertEqual(editor.search_match_count, 1)
        editor.backspace_search()  # "chor"
        editor.backspace_search()  # "cho"
        editor.backspace_search()  # "ch" — still 1 match ("chord")
        editor.backspace_search()  # "c" — now matches chord AND both bindings (command. / submit_cue)
        self.assertEqual(editor.search_buffer, "c")
        self.assertGreater(editor.search_match_count, 1)
        editor.backspace_search()  # ""
        # An empty buffer resets to zero matches (we don't advertise
        # every record as a "match" of an empty query).
        self.assertEqual(editor.search_buffer, "")
        self.assertEqual(editor.search_match_count, 0)

    def test_backspace_on_empty_buffer_is_noop(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.backspace_search()  # must not raise
        self.assertEqual(editor.search_buffer, "")
        self.assertTrue(editor.searching)

    def test_extend_without_begin_raises(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        with self.assertRaises(SettingsEditorError):
            editor.extend_search("a")

    def test_extend_rejects_multi_character(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        with self.assertRaises(ValueError):
            editor.extend_search("abc")

    def test_commit_search_leaves_cursor_on_match(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        for ch in "chord":
            editor.extend_search(ch)
        self.assertTrue(editor.commit_search())
        self.assertFalse(editor.searching)
        self.assertEqual(editor.state.section, Section.SOUNDS)
        self.assertEqual(editor.state.record_index, 1)

    def test_commit_search_preserves_matches_for_cycling(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.extend_search("a")
        editor.commit_search()
        self.assertGreater(editor.search_match_count, 1)

    def test_commit_without_search_returns_false(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        self.assertFalse(editor.commit_search())

    def test_cancel_restores_pre_search_cursor(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.enter()  # RECORD on voices[0]
        editor.enter()  # FIELD on voices[0].id
        editor.next()   # voices[0].engine
        origin_level = editor.state.level
        origin_section = editor.state.section
        origin_record = editor.state.record_index
        origin_field = editor.state.field_index
        editor.begin_search()
        for ch in "chord":
            editor.extend_search(ch)
        self.assertEqual(editor.state.section, Section.SOUNDS)

        self.assertTrue(editor.cancel_search())

        self.assertFalse(editor.searching)
        self.assertEqual(editor.state.level, origin_level)
        self.assertEqual(editor.state.section, origin_section)
        self.assertEqual(editor.state.record_index, origin_record)
        self.assertEqual(editor.state.field_index, origin_field)

    def test_cancel_without_search_returns_false(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        self.assertFalse(editor.cancel_search())

    def test_next_and_prev_search_match_cycle_matches(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.extend_search("a")  # matches narrator, alert, (chord?), bindings with voice_id=alert
        editor.commit_search()
        start = (editor.state.section, editor.state.record_index)
        editor.next_search_match()
        after_next = (editor.state.section, editor.state.record_index)
        self.assertNotEqual(start, after_next)
        editor.prev_search_match()
        self.assertEqual((editor.state.section, editor.state.record_index), start)

    def test_next_search_match_wraps_around(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        editor.begin_search()
        editor.extend_search("a")
        editor.commit_search()
        first = (editor.state.section, editor.state.record_index)
        count = editor.search_match_count
        for _ in range(count):
            editor.next_search_match()
        self.assertEqual((editor.state.section, editor.state.record_index), first)

    def test_next_match_without_any_matches_returns_false(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        self.assertFalse(editor.next_search_match())

    def test_search_update_event_carries_query_and_count(self) -> None:
        bus = EventBus()
        updates = _Recorder(bus, EventType.SETTINGS_SEARCH_UPDATED)
        editor = SettingsEditor(bus, self._search_bank())
        editor.begin_search()
        editor.extend_search("n")
        self.assertGreater(len(updates.events), 0)
        last = updates.events[-1].payload
        self.assertEqual(last["query"], "n")
        self.assertGreaterEqual(last["match_count"], 1)
        self.assertEqual(last["section"], "voices")

    def test_search_update_for_no_match_has_zero_count(self) -> None:
        bus = EventBus()
        updates = _Recorder(bus, EventType.SETTINGS_SEARCH_UPDATED)
        editor = SettingsEditor(bus, self._search_bank())
        editor.begin_search()
        editor.extend_search("z")
        last = updates.events[-1].payload
        self.assertEqual(last["match_count"], 0)
        self.assertNotIn("section", last)

    def test_commit_publishes_closed_with_committed_true(self) -> None:
        bus = EventBus()
        closed = _Recorder(bus, EventType.SETTINGS_SEARCH_CLOSED)
        editor = SettingsEditor(bus, self._search_bank())
        editor.begin_search()
        editor.extend_search("n")
        editor.commit_search()
        self.assertEqual(len(closed.events), 1)
        self.assertTrue(closed.events[0].payload["committed"])
        self.assertEqual(closed.events[0].payload["query"], "n")

    def test_cancel_publishes_closed_with_committed_false(self) -> None:
        bus = EventBus()
        closed = _Recorder(bus, EventType.SETTINGS_SEARCH_CLOSED)
        editor = SettingsEditor(bus, self._search_bank())
        editor.begin_search()
        editor.extend_search("n")
        editor.cancel_search()
        self.assertEqual(len(closed.events), 1)
        self.assertFalse(closed.events[0].payload["committed"])

    def test_focus_event_publishes_when_match_jumps_cursor(self) -> None:
        bus = EventBus()
        focused = _Recorder(bus, EventType.SETTINGS_FOCUSED)
        editor = SettingsEditor(bus, self._search_bank())
        before = len(focused.events)
        editor.begin_search()
        editor.extend_search("n")  # matches narrator
        self.assertGreater(len(focused.events), before)
        self.assertEqual(focused.events[-1].payload["level"], "record")
        self.assertEqual(focused.events[-1].payload["section"], "voices")

    def test_search_does_not_mutate_bank_or_dirty_flag(self) -> None:
        editor = SettingsEditor(EventBus(), self._search_bank())
        baseline = editor.bank
        editor.begin_search()
        for ch in "narrator":
            editor.extend_search(ch)
        editor.commit_search()
        self.assertIs(editor.bank, baseline)
        self.assertFalse(editor.state.dirty)


class SaveTests(unittest.TestCase):

    def test_save_writes_file_and_clears_dirty(self) -> None:
        bus = EventBus()
        saved = _Recorder(bus, EventType.SETTINGS_SAVED)
        editor = SettingsEditor(bus, _bank())
        editor.enter()
        editor.enter()
        editor.next()  # engine
        editor.edit("sapi")
        self.assertTrue(editor.state.dirty)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bank.json"
            editor.save(path)
            self.assertTrue(path.exists())
            reopened = SoundBank.load(path)
            self.assertEqual(reopened.voices[0].engine, "sapi")
        self.assertFalse(editor.state.dirty)
        self.assertEqual(len(saved.events), 1)


class DefaultBankIntegrationTests(unittest.TestCase):

    def test_editor_opens_over_the_default_bank(self) -> None:
        bus = EventBus()
        editor = SettingsEditor(bus, default_sound_bank())
        editor.enter()
        editor.enter()
        self.assertIn(editor.current_field_name(), VOICE_FIELDS)

    def test_editor_round_trips_full_default_bank_through_save_load(self) -> None:
        bus = EventBus()
        editor = SettingsEditor(bus, default_sound_bank())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "default.json"
            editor.save(path)
            reopened = SoundBank.load(path)
        self.assertEqual(reopened, default_sound_bank())


if __name__ == "__main__":
    unittest.main()
