"""Tests for StderrTailAnnouncer (F36 auto-read stderr tail on failure)."""

from __future__ import annotations

import unittest

from asat.error_tail import DEFAULT_TAIL_LINES, StderrTailAnnouncer
from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.output_buffer import OutputRecorder


class _Recorder:
    """Collect every event published on a bus for assertions."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


def _stream_stderr(bus: EventBus, cell_id: str, lines: list[str]) -> None:
    for line in lines:
        publish_event(
            bus,
            EventType.ERROR_CHUNK,
            {"cell_id": cell_id, "line": line},
            source="test",
        )


class StderrTailAnnouncerTests(unittest.TestCase):

    def _wire(self, *, tail_lines: int = DEFAULT_TAIL_LINES):
        bus = EventBus()
        output_recorder = OutputRecorder(bus)
        announcer = StderrTailAnnouncer(bus, output_recorder, tail_lines=tail_lines)
        event_recorder = _Recorder(bus)
        return bus, output_recorder, announcer, event_recorder

    def test_requires_positive_tail_lines(self) -> None:
        bus = EventBus()
        recorder = OutputRecorder(bus)
        with self.assertRaises(ValueError):
            StderrTailAnnouncer(bus, recorder, tail_lines=0)
        with self.assertRaises(ValueError):
            StderrTailAnnouncer(bus, recorder, tail_lines=-2)

    def test_publishes_tail_event_on_failure(self) -> None:
        bus, _, _, events = self._wire()
        _stream_stderr(bus, "c1", ["traceback", "  File foo.py", "NameError: x"])
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 1, "timed_out": False},
            source="kernel",
        )
        tail_events = events.of(EventType.COMMAND_FAILED_STDERR_TAIL)
        self.assertEqual(len(tail_events), 1)
        payload = tail_events[0].payload
        self.assertEqual(payload["cell_id"], "c1")
        self.assertEqual(payload["exit_code"], 1)
        self.assertFalse(payload["timed_out"])
        self.assertEqual(payload["tail_lines"], ["traceback", "  File foo.py", "NameError: x"])
        self.assertEqual(payload["tail_text"], "traceback\n  File foo.py\nNameError: x")
        self.assertEqual(payload["line_count"], 3)

    def test_tail_truncates_to_requested_count(self) -> None:
        bus, _, _, events = self._wire(tail_lines=2)
        _stream_stderr(bus, "c1", ["a", "b", "c", "d"])
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 2, "timed_out": False},
            source="kernel",
        )
        payload = events.of(EventType.COMMAND_FAILED_STDERR_TAIL)[0].payload
        self.assertEqual(payload["tail_lines"], ["c", "d"])
        self.assertEqual(payload["line_count"], 2)

    def test_fewer_stderr_lines_than_tail_limit(self) -> None:
        bus, _, _, events = self._wire(tail_lines=5)
        _stream_stderr(bus, "c1", ["only one"])
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 1, "timed_out": False},
            source="kernel",
        )
        payload = events.of(EventType.COMMAND_FAILED_STDERR_TAIL)[0].payload
        self.assertEqual(payload["tail_lines"], ["only one"])
        self.assertEqual(payload["tail_text"], "only one")

    def test_stdout_only_failures_do_not_publish(self) -> None:
        # A command that failed silently (exit code set, no stderr) has
        # nothing to read aloud — the regular failure chord + narration
        # is sufficient and the tail event stays quiet.
        bus, _, _, events = self._wire()
        publish_event(
            bus,
            EventType.OUTPUT_CHUNK,
            {"cell_id": "c1", "line": "hi"},
            source="test",
        )
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 1, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(events.of(EventType.COMMAND_FAILED_STDERR_TAIL), [])

    def test_no_buffer_for_cell_skips_silently(self) -> None:
        bus, _, _, events = self._wire()
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "never-produced-output", "exit_code": 1, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(events.of(EventType.COMMAND_FAILED_STDERR_TAIL), [])

    def test_missing_cell_id_is_ignored(self) -> None:
        bus, _, _, events = self._wire()
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"exit_code": 1, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(events.of(EventType.COMMAND_FAILED_STDERR_TAIL), [])

    def test_completed_events_do_not_trigger_tail(self) -> None:
        # The kernel routes non-zero exits to COMMAND_FAILED, so
        # COMMAND_COMPLETED should never produce a tail event. Even if
        # someone publishes a non-zero completed by mistake, the
        # announcer stays silent.
        bus, _, _, events = self._wire()
        _stream_stderr(bus, "c1", ["boom"])
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 7, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(events.of(EventType.COMMAND_FAILED_STDERR_TAIL), [])

    def test_timed_out_payload_is_passed_through(self) -> None:
        bus, _, _, events = self._wire()
        _stream_stderr(bus, "c1", ["slow build"])
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 124, "timed_out": True},
            source="kernel",
        )
        payload = events.of(EventType.COMMAND_FAILED_STDERR_TAIL)[0].payload
        self.assertTrue(payload["timed_out"])
        self.assertEqual(payload["exit_code"], 124)

    def test_source_identifies_announcer(self) -> None:
        bus, _, _, events = self._wire()
        _stream_stderr(bus, "c1", ["err"])
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 1, "timed_out": False},
            source="kernel",
        )
        tail_event = events.of(EventType.COMMAND_FAILED_STDERR_TAIL)[0]
        self.assertEqual(tail_event.source, "error_tail")

    def test_tail_lines_property_reports_configured_limit(self) -> None:
        bus = EventBus()
        recorder = OutputRecorder(bus)
        announcer = StderrTailAnnouncer(bus, recorder, tail_lines=7)
        self.assertEqual(announcer.tail_lines, 7)


if __name__ == "__main__":
    unittest.main()
