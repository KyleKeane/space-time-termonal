"""Unit tests for the F63 grouped event-log file."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from asat.event_bus import EventBus, publish_event
from asat.event_log_file import EventLogFile
from asat.events import Event, EventType


class EventLogFileTests(unittest.TestCase):

    def _drive(self, directory: Path, fire) -> Path:
        bus = EventBus()
        logger = EventLogFile(bus, directory)
        path = logger.current_path()
        fire(bus)
        logger.close()
        return path

    def test_writes_header_under_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "logs"

            def fire(bus: EventBus) -> None:
                publish_event(
                    bus,
                    EventType.KEY_PRESSED,
                    {"name": "ctrl_e", "char": ""},
                    source="test",
                )

            path = self._drive(directory, fire)
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("KEY_PRESSED", text)
            self.assertIn("name=ctrl_e", text)

    def test_key_pressed_groups_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)

            def fire(bus: EventBus) -> None:
                publish_event(
                    bus,
                    EventType.KEY_PRESSED,
                    {"name": "enter", "char": ""},
                    source="test",
                )
                publish_event(
                    bus,
                    EventType.FOCUS_CHANGED,
                    {"new_mode": "input"},
                    source="test",
                )
                publish_event(
                    bus,
                    EventType.HELP_REQUESTED,
                    {"lines": ["hi"]},
                    source="test",
                )

            path = self._drive(directory, fire)
            lines = path.read_text(encoding="utf-8").splitlines()
            # First line is the keystroke, starts at col 0.
            keystroke_line = next(
                line for line in lines if "KEY_PRESSED" in line
            )
            self.assertFalse(keystroke_line.startswith("  "))
            # Follow-on events are indented.
            focus_line = next(line for line in lines if "focus.changed" in line)
            self.assertTrue(focus_line.startswith("  "))

    def test_second_keystroke_starts_new_paragraph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)

            def fire(bus: EventBus) -> None:
                publish_event(
                    bus,
                    EventType.KEY_PRESSED,
                    {"name": "a", "char": "a"},
                    source="test",
                )
                publish_event(
                    bus,
                    EventType.KEY_PRESSED,
                    {"name": "b", "char": "b"},
                    source="test",
                )

            path = self._drive(directory, fire)
            text = path.read_text(encoding="utf-8")
            # Blank line between the two paragraphs.
            self.assertIn("\n\n", text)

    def test_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            logger = EventLogFile(bus, Path(tmp))
            logger.close()
            # A second close should not raise.
            logger.close()

    def test_current_path_uses_today(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            logger = EventLogFile(bus, Path(tmp))
            today = datetime.now().strftime("%Y-%m-%d")
            self.assertEqual(logger.current_path().name, f"events-{today}.log")
            logger.close()

    def test_string_values_are_repred(self) -> None:
        """Surrounding whitespace and quotes must survive to the log."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)

            def fire(bus: EventBus) -> None:
                publish_event(
                    bus,
                    EventType.HELP_REQUESTED,
                    {"note": "  spaced  "},
                    source="test",
                )

            path = self._drive(directory, fire)
            text = path.read_text(encoding="utf-8")
            self.assertIn("'  spaced  '", text)

    def test_no_keystroke_yet_children_unindented(self) -> None:
        """Events before the first keystroke land flush left."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)

            def fire(bus: EventBus) -> None:
                publish_event(
                    bus,
                    EventType.FOCUS_CHANGED,
                    {"new_mode": "notebook"},
                    source="test",
                )

            path = self._drive(directory, fire)
            lines = path.read_text(encoding="utf-8").splitlines()
            focus_line = next(line for line in lines if "focus.changed" in line)
            self.assertFalse(focus_line.startswith("  "))


if __name__ == "__main__":
    unittest.main()
