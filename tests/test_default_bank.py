"""Tests for the default SoundBank and its event coverage."""

from __future__ import annotations

import unittest

from asat.audio_sink import MemorySink
from asat.default_bank import COVERED_EVENT_TYPES, default_sound_bank
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.sound_bank import SCHEMA_VERSION, SoundBank
from asat.sound_engine import SoundEngine


# Representative payloads for every covered event. Keys match docs/EVENTS.md.
# The engine needs these so say_template placeholders resolve cleanly.
SAMPLE_PAYLOADS: dict[EventType, dict[str, object]] = {
    EventType.SESSION_CREATED: {"session_id": "s1"},
    EventType.SESSION_LOADED: {"session_id": "s1", "path": "/tmp/s.json"},
    EventType.SESSION_SAVED: {"session_id": "s1", "path": "/tmp/s.json"},
    EventType.CELL_CREATED: {"cell_id": "c1", "command": "ls"},
    EventType.CELL_UPDATED: {"cell_id": "c1", "command": "ls -la"},
    EventType.CELL_REMOVED: {"cell_id": "c1"},
    EventType.CELL_MOVED: {"cell_id": "c1", "old_index": 0, "new_index": 1},
    EventType.COMMAND_SUBMITTED: {"cell_id": "c1", "command": "ls"},
    EventType.COMMAND_STARTED: {"cell_id": "c1"},
    EventType.COMMAND_COMPLETED: {"cell_id": "c1", "exit_code": 0, "timed_out": False},
    EventType.COMMAND_COMPLETED_AWAY: {
        "cell_id": "c1",
        "current_cell_id": "c2",
        "original_event_type": "command.completed",
        "exit_code": 0,
        "timed_out": False,
    },
    EventType.COMMAND_FAILED: {"cell_id": "c1", "exit_code": 2, "timed_out": False},
    EventType.COMMAND_FAILED_STDERR_TAIL: {
        "cell_id": "c1",
        "exit_code": 1,
        "timed_out": False,
        "tail_lines": ["NameError: x"],
        "tail_text": "NameError: x",
        "line_count": 1,
    },
    EventType.COMMAND_CANCELLED: {"cell_id": "c1"},
    EventType.COMMAND_QUEUED: {"cell_id": "c1", "queue_depth": 1},
    EventType.QUEUE_DRAINED: {"last_cell_id": "c1", "queue_depth": 0},
    EventType.OUTPUT_CHUNK: {"cell_id": "c1", "line": "hello"},
    EventType.ERROR_CHUNK: {"cell_id": "c1", "line": "boom"},
    EventType.FOCUS_CHANGED: {
        "old_mode": "notebook",
        "new_mode": "input",
        "old_cell_id": None,
        "new_cell_id": "c1",
        "input_buffer": "",
        "transition": "mode",
        "command": "",
        "kind": "command",
        "heading_level": None,
        "heading_title": None,
    },
    EventType.OUTPUT_LINE_FOCUSED: {
        "cell_id": "c1",
        "line_number": 1,
        "stream": "stdout",
        "text": "hello",
    },
    EventType.ACTION_MENU_OPENED: {
        "focus_mode": "notebook",
        "cell_id": "c1",
        "item_ids": ["a"],
        "labels": ["copy"],
    },
    EventType.ACTION_MENU_CLOSED: {"focus_mode": "notebook", "cell_id": "c1"},
    EventType.ACTION_MENU_ITEM_FOCUSED: {"item_id": "copy", "label": "Copy", "index": 0},
    EventType.ACTION_MENU_ITEM_INVOKED: {
        "item_id": "copy",
        "label": "Copy",
        "focus_mode": "notebook",
        "cell_id": "c1",
    },
    EventType.CLIPBOARD_COPIED: {"cell_id": "c1", "source": "line", "length": 42},
    EventType.INTERACTIVE_MENU_DETECTED: {
        "cell_id": "c1",
        "detection": "reverse_video",
        "selected_index": 0,
        "selected_text": "option 1",
        "items": [],
    },
    EventType.INTERACTIVE_MENU_UPDATED: {
        "cell_id": "c1",
        "detection": "reverse_video",
        "selected_index": 1,
        "selected_text": "option 2",
        "items": [],
    },
    EventType.INTERACTIVE_MENU_CLEARED: {"cell_id": "c1"},
    EventType.SETTINGS_OPENED: {"section": "voices", "record_count": 3},
    EventType.SETTINGS_CLOSED: {"dirty": False},
    EventType.SETTINGS_FOCUSED: {
        "level": "field",
        "section": "voices",
        "record_index": 0,
        "record_id": "narrator",
        "field": "rate",
        "value": 1.0,
    },
    EventType.SETTINGS_VALUE_EDITED: {
        "section": "voices",
        "record_index": 0,
        "field": "rate",
        "old_value": 1.0,
        "new_value": 1.1,
    },
    EventType.SETTINGS_SAVED: {"path": "/tmp/bank.json"},
    EventType.SETTINGS_SEARCH_OPENED: {
        "origin_level": "section",
        "origin_section": "voices",
        "origin_record_index": 0,
        "origin_field_index": 0,
    },
    EventType.SETTINGS_SEARCH_UPDATED: {
        "query": "nar",
        "match_count": 1,
        "section": "voices",
        "record_index": 0,
        "record_id": "narrator",
    },
    EventType.SETTINGS_SEARCH_CLOSED: {
        "query": "nar",
        "match_count": 1,
        "committed": True,
    },
    EventType.SETTINGS_RESET_OPENED: {
        "scope": "section",
        "section": "voices",
        "target_count": 3,
    },
    EventType.SETTINGS_RESET_CLOSED: {
        "scope": "section",
        "committed": True,
        "changed": True,
        "outcome": "applied",
    },
    EventType.HELP_REQUESTED: {"lines": ["help"]},
    EventType.PROMPT_REFRESH: {
        "last_exit_code": 1,
        "last_cell_id": "c1",
        "last_timed_out": False,
        "cwd": "/tmp",
    },
    EventType.FIRST_RUN_DETECTED: {
        "lines": ["Welcome."],
        "sentinel_path": "/tmp/first-run-done",
    },
    EventType.WORKSPACE_OPENED: {
        "root": "/tmp/proj",
        "name": "proj",
        "notebook_count": 2,
    },
    EventType.NOTEBOOK_OPENED: {
        "path": "/tmp/proj/notebooks/default.asatnb",
        "name": "default",
    },
    EventType.NOTEBOOK_CREATED: {
        "path": "/tmp/proj/notebooks/ideas.asatnb",
        "name": "ideas",
    },
    EventType.NOTEBOOK_LISTED: {
        "names": ["default", "ideas"],
        "summary": "two notebooks: default, ideas",
    },
}


class DefaultBankStructureTests(unittest.TestCase):

    def test_default_bank_validates(self) -> None:
        bank = default_sound_bank()
        bank.validate()  # Would raise if references are broken.
        self.assertEqual(bank.version, SCHEMA_VERSION)

    def test_default_bank_has_three_voices(self) -> None:
        bank = default_sound_bank()
        ids = {voice.id for voice in bank.voices}
        self.assertEqual(ids, {"narrator", "alert", "system"})

    def test_default_bank_sound_ids_are_unique(self) -> None:
        bank = default_sound_bank()
        ids = [sound.id for sound in bank.sounds]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_bank_binding_ids_are_unique(self) -> None:
        bank = default_sound_bank()
        ids = [binding.id for binding in bank.bindings]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_bank_round_trips_through_json(self) -> None:
        bank = default_sound_bank()
        restored = SoundBank.from_dict(bank.to_dict())
        self.assertEqual(restored, bank)


class CoverageTests(unittest.TestCase):

    def test_every_covered_type_has_at_least_one_binding(self) -> None:
        bank = default_sound_bank()
        bound = {binding.event_type for binding in bank.bindings}
        for event_type in COVERED_EVENT_TYPES:
            self.assertIn(event_type.value, bound, event_type.value)

    def test_every_sample_payload_is_for_a_covered_type(self) -> None:
        for event_type in SAMPLE_PAYLOADS:
            self.assertIn(event_type, COVERED_EVENT_TYPES)

    def test_every_covered_type_has_sample_payload(self) -> None:
        for event_type in COVERED_EVENT_TYPES:
            self.assertIn(event_type, SAMPLE_PAYLOADS, event_type.value)

    def test_unbound_event_types_are_intentionally_silent(self) -> None:
        unbound = set(EventType) - COVERED_EVENT_TYPES - {
            EventType.AUDIO_SPOKEN,
            EventType.AUDIO_INTERRUPTED,
            EventType.NARRATION_REPLAYED,
        }
        # Explicit allow-list: anything else showing up here is a new
        # EventType the maintainer must decide about.
        self.assertEqual(
            unbound,
            {
                EventType.KEY_PRESSED,
                EventType.ACTION_INVOKED,
                EventType.OUTPUT_LINE_APPENDED,
                EventType.SCREEN_UPDATED,
                EventType.ANSI_CURSOR_MOVED,
                EventType.ANSI_SGR_CHANGED,
                EventType.ANSI_DISPLAY_CLEARED,
                EventType.ANSI_LINE_ERASED,
                EventType.ANSI_OSC_RECEIVED,
                EventType.ANSI_BELL,
            },
        )


class DefaultBankSmokeTests(unittest.TestCase):

    def test_engine_plays_something_for_every_covered_event(self) -> None:
        bus = EventBus()
        sink = MemorySink()
        engine = SoundEngine(bus, default_sound_bank(), sink, sample_rate=8000)
        self.addCleanup(engine.close)

        for event_type in COVERED_EVENT_TYPES:
            sink.reset()
            publish_event(bus, event_type, dict(SAMPLE_PAYLOADS[event_type]), source="test")
            self.assertGreaterEqual(
                len(sink.buffers),
                1,
                f"expected at least one buffer for {event_type.value}",
            )

    def test_predicate_branches_pick_the_right_message(self) -> None:
        bus = EventBus()
        sink = MemorySink()
        engine = SoundEngine(bus, default_sound_bank(), sink, sample_rate=8000)
        self.addCleanup(engine.close)

        spoken_labels: list[str] = []

        def capture(event):
            spoken_labels.append(event.payload["binding_id"])

        bus.subscribe(EventType.AUDIO_SPOKEN, capture)

        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0, "timed_out": False},
            source="kernel",
        )
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 3, "timed_out": False},
            source="kernel",
        )
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 124, "timed_out": True},
            source="kernel",
        )
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 1, "timed_out": False},
            source="kernel",
        )

        self.assertEqual(
            spoken_labels,
            [
                "command_completed_ok",
                "command_completed_nonzero",
                "command_failed_timeout",
                "command_failed_generic",
            ],
        )

    def test_focus_changed_cell_branches_by_kind(self) -> None:
        """F61: heading landings narrate the heading; command landings
        narrate the command. The two branches are mutually exclusive so
        the user never hears both."""
        bus = EventBus()
        sink = MemorySink()
        engine = SoundEngine(bus, default_sound_bank(), sink, sample_rate=8000)
        self.addCleanup(engine.close)

        spoken_labels: list[str] = []
        bus.subscribe(
            EventType.AUDIO_SPOKEN,
            lambda event: spoken_labels.append(event.payload["binding_id"]),
        )

        publish_event(
            bus,
            EventType.FOCUS_CHANGED,
            {
                "old_mode": "notebook",
                "new_mode": "notebook",
                "old_cell_id": "c0",
                "new_cell_id": "c1",
                "input_buffer": "",
                "transition": "cell",
                "command": "",
                "kind": "heading",
                "heading_level": 2,
                "heading_title": "Setup",
            },
            source="notebook",
        )
        publish_event(
            bus,
            EventType.FOCUS_CHANGED,
            {
                "old_mode": "notebook",
                "new_mode": "notebook",
                "old_cell_id": "c1",
                "new_cell_id": "c2",
                "input_buffer": "",
                "transition": "cell",
                "command": "ls",
                "kind": "command",
                "heading_level": None,
                "heading_title": None,
            },
            source="notebook",
        )

        self.assertEqual(
            spoken_labels,
            ["focus_changed_heading", "focus_changed_cell"],
        )

    def test_settings_reset_outcome_branches_each_pick_a_binding(self) -> None:
        """F21c: applied / already_default / cancelled each map to a
        distinct binding so the user hears the right feedback."""
        bus = EventBus()
        sink = MemorySink()
        engine = SoundEngine(bus, default_sound_bank(), sink, sample_rate=8000)
        self.addCleanup(engine.close)

        spoken_labels: list[str] = []
        bus.subscribe(
            EventType.AUDIO_SPOKEN,
            lambda event: spoken_labels.append(event.payload["binding_id"]),
        )

        for outcome in ("applied", "already_default", "cancelled"):
            publish_event(
                bus,
                EventType.SETTINGS_RESET_CLOSED,
                {
                    "scope": "section",
                    "committed": outcome != "cancelled",
                    "changed": outcome == "applied",
                    "outcome": outcome,
                },
                source="test",
            )

        self.assertEqual(
            spoken_labels,
            [
                "settings_reset_applied",
                "settings_reset_already_default",
                "settings_reset_cancelled",
            ],
        )


if __name__ == "__main__":
    unittest.main()
