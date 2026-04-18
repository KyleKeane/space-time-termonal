"""Unit tests for OutputBuffer and OutputRecorder."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.output_buffer import (
    OutputBuffer,
    OutputLine,
    OutputRecorder,
    STDERR,
    STDOUT,
)


class _Recorder:
    """Collects every event on a bus for assertions."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


class OutputBufferTests(unittest.TestCase):

    def test_append_returns_numbered_line(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        line = buffer.append("hello")
        self.assertIsInstance(line, OutputLine)
        self.assertEqual(line.cell_id, "c1")
        self.assertEqual(line.line_number, 0)
        self.assertEqual(line.stream, STDOUT)
        self.assertEqual(line.text, "hello")

    def test_append_numbers_increment(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        for text in ["a", "b", "c"]:
            buffer.append(text)
        self.assertEqual(len(buffer), 3)
        numbers = [line.line_number for line in buffer]
        self.assertEqual(numbers, [0, 1, 2])

    def test_append_to_unknown_stream_raises(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        with self.assertRaises(ValueError):
            buffer.append("x", stream="log")

    def test_line_out_of_range_raises(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        buffer.append("only")
        with self.assertRaises(IndexError):
            buffer.line(5)

    def test_page_clamps_at_end(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        for text in ["one", "two", "three"]:
            buffer.append(text)
        page = buffer.page(start=2, size=5)
        self.assertEqual([line.text for line in page], ["three"])

    def test_page_rejects_nonpositive_size(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        with self.assertRaises(ValueError):
            buffer.page(start=0, size=0)

    def test_lines_on_stream_filters_by_stream(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        buffer.append("out-a", stream=STDOUT)
        buffer.append("err-a", stream=STDERR)
        buffer.append("out-b", stream=STDOUT)
        errs = buffer.lines_on_stream(STDERR)
        self.assertEqual([line.text for line in errs], ["err-a"])

    def test_clear_drops_all_lines(self) -> None:
        buffer = OutputBuffer(cell_id="c1")
        buffer.append("a")
        buffer.append("b")
        buffer.clear()
        self.assertEqual(len(buffer), 0)


class OutputRecorderTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = OutputRecorder(self.bus)
        self.events = _Recorder(self.bus)

    def _publish_chunk(self, event_type: EventType, cell_id: str, line: str) -> None:
        self.bus.publish(
            Event(
                event_type=event_type,
                payload={"cell_id": cell_id, "line": line},
                source="test",
            )
        )

    def test_output_chunk_creates_buffer_on_first_event(self) -> None:
        self.assertFalse(self.recorder.has_buffer_for("c1"))
        self._publish_chunk(EventType.OUTPUT_CHUNK, "c1", "hello")
        self.assertTrue(self.recorder.has_buffer_for("c1"))
        buffer = self.recorder.buffer_for("c1")
        self.assertEqual(len(buffer), 1)
        self.assertEqual(buffer.line(0).text, "hello")
        self.assertEqual(buffer.line(0).stream, STDOUT)

    def test_error_chunk_labels_line_as_stderr(self) -> None:
        self._publish_chunk(EventType.ERROR_CHUNK, "c1", "nope")
        buffer = self.recorder.buffer_for("c1")
        self.assertEqual(buffer.line(0).stream, STDERR)

    def test_multiple_cells_get_independent_buffers(self) -> None:
        self._publish_chunk(EventType.OUTPUT_CHUNK, "c1", "from c1")
        self._publish_chunk(EventType.OUTPUT_CHUNK, "c2", "from c2")
        self.assertEqual(self.recorder.buffer_for("c1").line(0).text, "from c1")
        self.assertEqual(self.recorder.buffer_for("c2").line(0).text, "from c2")

    def test_records_are_mirrored_as_output_line_appended(self) -> None:
        self._publish_chunk(EventType.OUTPUT_CHUNK, "c1", "first")
        self._publish_chunk(EventType.ERROR_CHUNK, "c1", "second")
        appended = self.events.of(EventType.OUTPUT_LINE_APPENDED)
        self.assertEqual(len(appended), 2)
        self.assertEqual(appended[0].payload["line_number"], 0)
        self.assertEqual(appended[0].payload["stream"], STDOUT)
        self.assertEqual(appended[1].payload["line_number"], 1)
        self.assertEqual(appended[1].payload["stream"], STDERR)

    def test_malformed_events_are_ignored(self) -> None:
        self.bus.publish(
            Event(
                event_type=EventType.OUTPUT_CHUNK,
                payload={"cell_id": None, "line": "skip"},
                source="test",
            )
        )
        self.bus.publish(
            Event(
                event_type=EventType.OUTPUT_CHUNK,
                payload={"cell_id": "c1"},
                source="test",
            )
        )
        self.assertFalse(self.recorder.has_buffer_for("c1"))
        self.assertEqual(self.events.of(EventType.OUTPUT_LINE_APPENDED), [])

    def test_discard_removes_buffer(self) -> None:
        self._publish_chunk(EventType.OUTPUT_CHUNK, "c1", "hi")
        dropped = self.recorder.discard("c1")
        self.assertIsNotNone(dropped)
        self.assertFalse(self.recorder.has_buffer_for("c1"))


if __name__ == "__main__":
    unittest.main()
