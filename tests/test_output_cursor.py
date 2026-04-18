"""Unit tests for OutputCursor."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.output_buffer import OutputBuffer, STDERR, STDOUT
from asat.output_cursor import OutputCursor


class _Recorder:
    """Collects FOCUS events fired by the cursor for assertions."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(EventType.OUTPUT_LINE_FOCUSED, self.events.append)


def _buffer_with(cell_id: str, lines: list[tuple[str, str]]) -> OutputBuffer:
    """Create a buffer pre-populated with (text, stream) entries."""
    buffer = OutputBuffer(cell_id=cell_id)
    for text, stream in lines:
        buffer.append(text, stream=stream)
    return buffer


class AttachAndNavigateTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = _Recorder(self.bus)
        self.cursor = OutputCursor(self.bus, page_size=3)
        self.buffer = _buffer_with(
            "c1",
            [
                ("one", STDOUT),
                ("two", STDOUT),
                ("three", STDERR),
                ("four", STDOUT),
                ("five", STDOUT),
            ],
        )

    def test_attach_snaps_to_last_line(self) -> None:
        line = self.cursor.attach(self.buffer)
        assert line is not None
        self.assertEqual(line.text, "five")
        self.assertEqual(self.cursor.line_number, 4)
        self.assertEqual(len(self.recorder.events), 1)

    def test_attach_on_empty_buffer_returns_none(self) -> None:
        empty = OutputBuffer(cell_id="empty")
        result = self.cursor.attach(empty)
        self.assertIsNone(result)
        self.assertIsNone(self.cursor.line_number)
        self.assertEqual(self.recorder.events, [])

    def test_move_line_up_walks_toward_start(self) -> None:
        self.cursor.attach(self.buffer)
        self.cursor.move_line_up()
        self.cursor.move_line_up()
        self.assertEqual(self.cursor.line_number, 2)
        self.assertEqual(self.cursor.current_line().text, "three")

    def test_move_line_up_clamps_at_top(self) -> None:
        self.cursor.attach(self.buffer)
        self.cursor.move_to_start()
        self.recorder.events.clear()
        result = self.cursor.move_line_up()
        assert result is not None
        self.assertEqual(result.line_number, 0)
        self.assertEqual(self.recorder.events, [])

    def test_move_line_down_clamps_at_bottom(self) -> None:
        self.cursor.attach(self.buffer)
        self.recorder.events.clear()
        result = self.cursor.move_line_down()
        assert result is not None
        self.assertEqual(result.line_number, 4)
        self.assertEqual(self.recorder.events, [])

    def test_page_up_jumps_by_page_size(self) -> None:
        self.cursor.attach(self.buffer)
        self.cursor.move_page_up()
        self.assertEqual(self.cursor.line_number, 1)

    def test_page_down_clamps_within_buffer(self) -> None:
        self.cursor.attach(self.buffer)
        self.cursor.move_to_start()
        self.cursor.move_page_down()
        self.assertEqual(self.cursor.line_number, 3)
        self.cursor.move_page_down()
        self.assertEqual(self.cursor.line_number, 4)

    def test_move_to_start_and_end(self) -> None:
        self.cursor.attach(self.buffer)
        self.cursor.move_to_start()
        self.assertEqual(self.cursor.line_number, 0)
        self.cursor.move_to_end()
        self.assertEqual(self.cursor.line_number, 4)


class DetachedCursorTests(unittest.TestCase):

    def test_detached_cursor_ignores_motion(self) -> None:
        bus = EventBus()
        cursor = OutputCursor(bus)
        self.assertIsNone(cursor.move_line_up())
        self.assertIsNone(cursor.move_line_down())
        self.assertIsNone(cursor.move_to_start())
        self.assertIsNone(cursor.current_line())

    def test_detach_clears_state(self) -> None:
        bus = EventBus()
        cursor = OutputCursor(bus)
        buffer = _buffer_with("c1", [("a", STDOUT)])
        cursor.attach(buffer)
        cursor.detach()
        self.assertIsNone(cursor.buffer)
        self.assertIsNone(cursor.line_number)

    def test_invalid_page_size_rejected(self) -> None:
        with self.assertRaises(ValueError):
            OutputCursor(EventBus(), page_size=0)


class FocusEventTests(unittest.TestCase):

    def test_focus_event_contains_line_metadata(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        cursor = OutputCursor(bus)
        buffer = _buffer_with(
            "c1",
            [("first", STDOUT), ("second", STDERR)],
        )
        cursor.attach(buffer)
        cursor.move_line_up()
        payloads = [event.payload for event in recorder.events]
        self.assertEqual(payloads[0]["line_number"], 1)
        self.assertEqual(payloads[0]["stream"], STDERR)
        self.assertEqual(payloads[1]["line_number"], 0)
        self.assertEqual(payloads[1]["stream"], STDOUT)
        self.assertEqual(payloads[1]["text"], "first")


if __name__ == "__main__":
    unittest.main()
