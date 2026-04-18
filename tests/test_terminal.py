"""Unit tests for the TerminalRenderer.

The renderer is deterministic: every line it produces is a function
of one event. We exercise it by publishing hand-crafted events into a
bus it is attached to, and asserting the captured stream contents.
"""

from __future__ import annotations

import io
import unittest

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.terminal import TerminalRenderer


class TerminalRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.stream = io.StringIO()
        self.renderer = TerminalRenderer(self.bus, stream=self.stream)

    def _emit(self, event_type: EventType, payload: dict) -> None:
        publish_event(self.bus, event_type, payload, source="test")

    def test_session_created_writes_a_banner(self) -> None:
        self._emit(EventType.SESSION_CREATED, {"session_id": "abc123"})
        self.assertIn("session abc123 ready", self.stream.getvalue())

    def test_session_saved_writes_the_path(self) -> None:
        self._emit(EventType.SESSION_SAVED, {"path": "/tmp/s.json"})
        self.assertIn("/tmp/s.json", self.stream.getvalue())

    def test_focus_changed_renders_mode_and_short_cell_id(self) -> None:
        self._emit(
            EventType.FOCUS_CHANGED,
            {"new_mode": "input", "new_cell_id": "abcdef1234"},
        )
        self.assertIn("[input #abcdef]", self.stream.getvalue())

    def test_insert_character_echoes_the_literal_char(self) -> None:
        self._emit(
            EventType.ACTION_INVOKED,
            {"action": "insert_character", "char": "h"},
        )
        self._emit(
            EventType.ACTION_INVOKED,
            {"action": "insert_character", "char": "i"},
        )
        self.assertIn("hi", self.stream.getvalue())

    def test_submit_writes_a_prompt_line_with_the_command(self) -> None:
        self._emit(
            EventType.ACTION_INVOKED,
            {"action": "submit", "command": "echo hi"},
        )
        self.assertIn("$ echo hi", self.stream.getvalue())

    def test_meta_submit_is_not_rendered_as_a_command(self) -> None:
        self._emit(
            EventType.ACTION_INVOKED,
            {"action": "submit", "command": ":quit", "meta_command": "quit"},
        )
        self.assertNotIn("$ :quit", self.stream.getvalue())

    def test_output_chunk_prints_the_line(self) -> None:
        self._emit(EventType.OUTPUT_CHUNK, {"line": "hello world"})
        self.assertIn("hello world", self.stream.getvalue())

    def test_error_chunk_is_prefixed(self) -> None:
        self._emit(EventType.ERROR_CHUNK, {"line": "boom"})
        self.assertIn("! boom", self.stream.getvalue())

    def test_command_completed_shows_exit_code(self) -> None:
        self._emit(
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0, "timed_out": False},
        )
        self.assertIn("[done exit=0]", self.stream.getvalue())

    def test_command_failed_with_launch_error_renders_the_error(self) -> None:
        self._emit(
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "error": "No such file: frobnicate"},
        )
        self.assertIn("[failed: No such file: frobnicate]", self.stream.getvalue())


if __name__ == "__main__":
    unittest.main()
