"""Unit tests for asat/completion_alert.py (F34)."""

from __future__ import annotations

import unittest
from typing import Any

from asat.completion_alert import CompletionFocusWatcher
from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType


def _capture(bus: EventBus, event_type: EventType) -> list[Event]:
    captured: list[Event] = []
    bus.subscribe(event_type, captured.append)
    return captured


def _focus_event(new_cell_id: str) -> dict[str, Any]:
    # Minimum shape the watcher reads; the rest matches the real
    # NotebookCursor payload so regressions surface here too.
    return {
        "old_mode": "notebook",
        "new_mode": "input",
        "old_cell_id": None,
        "new_cell_id": new_cell_id,
        "input_buffer": "",
        "transition": "mode",
        "command": "",
    }


class CompletionFocusWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.watcher = CompletionFocusWatcher(self.bus)
        self.away = _capture(self.bus, EventType.COMMAND_COMPLETED_AWAY)

    def test_completion_on_focused_cell_does_not_fire_away(self) -> None:
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c1"), source="test")
        publish_event(
            self.bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(self.away, [])

    def test_completion_on_other_cell_fires_away(self) -> None:
        # User started a command on c1, then moved to c2 while it ran.
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c1"), source="test")
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c2"), source="test")
        publish_event(
            self.bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(len(self.away), 1)
        payload = self.away[0].payload
        self.assertEqual(payload["cell_id"], "c1")
        self.assertEqual(payload["current_cell_id"], "c2")
        self.assertEqual(payload["original_event_type"], "command.completed")
        self.assertEqual(payload["exit_code"], 0)

    def test_failure_on_other_cell_fires_away_with_original_type(self) -> None:
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c1"), source="test")
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c2"), source="test")
        publish_event(
            self.bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 2, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(len(self.away), 1)
        self.assertEqual(self.away[0].payload["original_event_type"], "command.failed")

    def test_completion_with_no_focus_history_does_not_fire_away(self) -> None:
        # Before any FOCUS_CHANGED event, the watcher has no basis to
        # compare — defaulting to "away" would flood every first
        # command with an away nudge, so we require a known focus.
        publish_event(
            self.bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(self.away, [])

    def test_completion_without_cell_id_is_ignored(self) -> None:
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c2"), source="test")
        publish_event(
            self.bus,
            EventType.COMMAND_COMPLETED,
            {"exit_code": 0, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(self.away, [])

    def test_current_cell_id_tracks_focus(self) -> None:
        self.assertIsNone(self.watcher.current_cell_id)
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c3"), source="test")
        self.assertEqual(self.watcher.current_cell_id, "c3")

    def test_away_event_carries_timed_out_flag(self) -> None:
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c1"), source="test")
        publish_event(self.bus, EventType.FOCUS_CHANGED, _focus_event("c2"), source="test")
        publish_event(
            self.bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 1, "timed_out": True},
            source="kernel",
        )
        self.assertEqual(self.away[0].payload["timed_out"], True)


if __name__ == "__main__":
    unittest.main()
