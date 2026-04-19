"""Unit tests for asat/jsonl_logger.py (F22)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.jsonl_logger import JsonlEventLogger


class JsonlEventLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "events.jsonl"
        self.bus = EventBus()

    def _read_records(self) -> list[dict]:
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_logger_writes_one_line_per_event(self) -> None:
        logger = JsonlEventLogger(self.bus, self.path)
        self.addCleanup(logger.close)
        publish_event(self.bus, EventType.SESSION_CREATED, {"session_id": "s1"}, source="app")
        publish_event(self.bus, EventType.CELL_CREATED, {"cell_id": "c1", "command": "ls"}, source="app")
        records = self._read_records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event_type"], "session.created")
        self.assertEqual(records[0]["payload"], {"session_id": "s1"})
        self.assertEqual(records[0]["source"], "app")
        self.assertIn("timestamp", records[0])

    def test_logger_subscribes_via_wildcard_and_captures_every_type(self) -> None:
        logger = JsonlEventLogger(self.bus, self.path)
        self.addCleanup(logger.close)
        for event_type, payload in (
            (EventType.SESSION_CREATED, {"session_id": "s1"}),
            (EventType.FOCUS_CHANGED, {"new_mode": "input", "new_cell_id": "c1"}),
            (EventType.AUDIO_SPOKEN, {"text": "hello"}),
            (EventType.COMMAND_COMPLETED, {"cell_id": "c1", "exit_code": 0}),
        ):
            publish_event(self.bus, event_type, payload, source="test")
        types = [r["event_type"] for r in self._read_records()]
        self.assertEqual(types, [
            "session.created",
            "focus.changed",
            "audio.spoken",
            "command.completed",
        ])

    def test_logger_truncates_existing_file_on_open(self) -> None:
        # A long-running install should never grow an unbounded log:
        # each session starts from a clean file.
        self.path.write_text("old stale content that should vanish\n", encoding="utf-8")
        logger = JsonlEventLogger(self.bus, self.path)
        self.addCleanup(logger.close)
        publish_event(self.bus, EventType.SESSION_CREATED, {"session_id": "s1"}, source="app")
        records = self._read_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event_type"], "session.created")

    def test_logger_creates_parent_directory(self) -> None:
        nested = Path(self._tmpdir.name) / "nested" / "deeper" / "events.jsonl"
        logger = JsonlEventLogger(self.bus, nested)
        self.addCleanup(logger.close)
        publish_event(self.bus, EventType.SESSION_CREATED, {"session_id": "s1"}, source="app")
        self.assertTrue(nested.exists())

    def test_logger_unsubscribes_on_close(self) -> None:
        logger = JsonlEventLogger(self.bus, self.path)
        logger.close()
        publish_event(self.bus, EventType.SESSION_CREATED, {"session_id": "s1"}, source="app")
        # File is closed and subscriber is gone, so no new events land.
        self.assertEqual(self._read_records(), [])

    def test_logger_close_is_idempotent(self) -> None:
        logger = JsonlEventLogger(self.bus, self.path)
        logger.close()
        logger.close()

    def test_logger_falls_back_to_repr_for_unserialisable_payloads(self) -> None:
        logger = JsonlEventLogger(self.bus, self.path)
        self.addCleanup(logger.close)

        class Weird:
            def __repr__(self) -> str:
                return "<Weird>"

        publish_event(self.bus, EventType.HELP_REQUESTED, {"obj": Weird()}, source="test")
        records = self._read_records()
        self.assertEqual(records[0]["payload"]["obj"], "<Weird>")


if __name__ == "__main__":
    unittest.main()
