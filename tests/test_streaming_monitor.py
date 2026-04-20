"""Unit tests for asat/streaming_monitor.py (F37)."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.streaming_monitor import StreamingMonitor


class _Clock:
    """Mutable virtual clock for deterministic pacing tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _capture(bus: EventBus, event_type: EventType) -> list[Event]:
    captured: list[Event] = []
    bus.subscribe(event_type, captured.append)
    return captured


class StreamingMonitorSilenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.clock = _Clock()
        self.monitor = StreamingMonitor(
            self.bus,
            silence_threshold_sec=5.0,
            progress_beat_interval_sec=30.0,
            clock=self.clock,
        )
        self.paused = _capture(self.bus, EventType.OUTPUT_STREAM_PAUSED)
        self.beats = _capture(self.bus, EventType.OUTPUT_STREAM_BEAT)

    def _start(self, cell_id: str = "c1") -> None:
        publish_event(
            self.bus,
            EventType.COMMAND_STARTED,
            {"cell_id": cell_id},
            source="test",
        )

    def _chunk(self, cell_id: str = "c1") -> None:
        publish_event(
            self.bus,
            EventType.OUTPUT_CHUNK,
            {"cell_id": cell_id, "line": "x"},
            source="test",
        )

    def _complete(self, cell_id: str = "c1") -> None:
        publish_event(
            self.bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": cell_id, "exit_code": 0, "timed_out": False},
            source="test",
        )

    def test_no_events_before_any_command_starts(self) -> None:
        self.clock.advance(3600.0)
        self.monitor.check()
        self.assertEqual(self.paused, [])
        self.assertEqual(self.beats, [])

    def test_silence_fires_once_per_quiet_window(self) -> None:
        self._start()
        self.clock.advance(4.0)
        self.monitor.check()
        self.assertEqual(self.paused, [])
        self.clock.advance(2.0)
        self.monitor.check()
        self.assertEqual(len(self.paused), 1)
        self.assertEqual(self.paused[0].payload["cell_id"], "c1")
        self.assertGreaterEqual(self.paused[0].payload["gap_sec"], 5.0)
        # A second check with no fresh chunk should not re-fire.
        self.clock.advance(10.0)
        self.monitor.check()
        self.assertEqual(len(self.paused), 1)

    def test_chunk_rearms_silence_gate(self) -> None:
        self._start()
        self.clock.advance(6.0)
        self.monitor.check()
        self.assertEqual(len(self.paused), 1)
        self._chunk()
        self.clock.advance(6.0)
        self.monitor.check()
        self.assertEqual(len(self.paused), 2)

    def test_chunk_before_threshold_suppresses_pause(self) -> None:
        self._start()
        self.clock.advance(3.0)
        self._chunk()
        self.clock.advance(3.0)
        self.monitor.check()
        self.assertEqual(self.paused, [])

    def test_completion_stops_tracking(self) -> None:
        self._start()
        self._complete()
        self.clock.advance(60.0)
        self.monitor.check()
        self.assertEqual(self.paused, [])
        self.assertEqual(self.beats, [])
        self.assertIsNone(self.monitor.active_cell_id)


class StreamingMonitorBeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.clock = _Clock()
        self.monitor = StreamingMonitor(
            self.bus,
            silence_threshold_sec=5.0,
            progress_beat_interval_sec=10.0,
            clock=self.clock,
        )
        self.beats = _capture(self.bus, EventType.OUTPUT_STREAM_BEAT)

    def test_beat_fires_on_each_interval(self) -> None:
        publish_event(
            self.bus,
            EventType.COMMAND_STARTED,
            {"cell_id": "c1"},
            source="test",
        )
        self.clock.advance(9.0)
        self.monitor.check()
        self.assertEqual(self.beats, [])
        self.clock.advance(2.0)
        self.monitor.check()
        self.assertEqual(len(self.beats), 1)
        self.assertEqual(self.beats[0].payload["cell_id"], "c1")
        self.assertGreaterEqual(self.beats[0].payload["elapsed_sec"], 10.0)
        self.clock.advance(10.0)
        self.monitor.check()
        self.assertEqual(len(self.beats), 2)

    def test_beat_payload_tracks_elapsed_since_start(self) -> None:
        publish_event(
            self.bus,
            EventType.COMMAND_STARTED,
            {"cell_id": "c1"},
            source="test",
        )
        self.clock.advance(25.0)
        self.monitor.check()
        self.assertEqual(len(self.beats), 1)
        self.assertAlmostEqual(self.beats[0].payload["elapsed_sec"], 25.0)


class StreamingMonitorLifecycleTests(unittest.TestCase):
    def test_error_chunk_resets_silence_timer(self) -> None:
        bus = EventBus()
        clock = _Clock()
        monitor = StreamingMonitor(
            bus,
            silence_threshold_sec=5.0,
            progress_beat_interval_sec=30.0,
            clock=clock,
        )
        paused = _capture(bus, EventType.OUTPUT_STREAM_PAUSED)
        publish_event(bus, EventType.COMMAND_STARTED, {"cell_id": "c1"}, source="test")
        clock.advance(3.0)
        publish_event(
            bus,
            EventType.ERROR_CHUNK,
            {"cell_id": "c1", "line": "oops"},
            source="test",
        )
        clock.advance(3.0)
        monitor.check()
        self.assertEqual(paused, [])
        clock.advance(3.0)
        monitor.check()
        self.assertEqual(len(paused), 1)

    def test_chunk_for_other_cell_is_ignored(self) -> None:
        bus = EventBus()
        clock = _Clock()
        monitor = StreamingMonitor(
            bus,
            silence_threshold_sec=5.0,
            progress_beat_interval_sec=30.0,
            clock=clock,
        )
        paused = _capture(bus, EventType.OUTPUT_STREAM_PAUSED)
        publish_event(bus, EventType.COMMAND_STARTED, {"cell_id": "c1"}, source="test")
        clock.advance(3.0)
        publish_event(
            bus,
            EventType.OUTPUT_CHUNK,
            {"cell_id": "other", "line": "x"},
            source="test",
        )
        clock.advance(3.0)
        monitor.check()
        self.assertEqual(len(paused), 1)

    def test_cancelled_stops_tracking(self) -> None:
        bus = EventBus()
        clock = _Clock()
        monitor = StreamingMonitor(
            bus,
            silence_threshold_sec=5.0,
            progress_beat_interval_sec=30.0,
            clock=clock,
        )
        paused = _capture(bus, EventType.OUTPUT_STREAM_PAUSED)
        publish_event(bus, EventType.COMMAND_STARTED, {"cell_id": "c1"}, source="test")
        publish_event(bus, EventType.COMMAND_CANCELLED, {"cell_id": "c1"}, source="test")
        clock.advance(60.0)
        monitor.check()
        self.assertEqual(paused, [])

    def test_failed_stops_tracking(self) -> None:
        bus = EventBus()
        clock = _Clock()
        monitor = StreamingMonitor(
            bus,
            silence_threshold_sec=5.0,
            progress_beat_interval_sec=30.0,
            clock=clock,
        )
        beats = _capture(bus, EventType.OUTPUT_STREAM_BEAT)
        publish_event(bus, EventType.COMMAND_STARTED, {"cell_id": "c1"}, source="test")
        publish_event(
            bus,
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 2, "timed_out": False},
            source="test",
        )
        clock.advance(60.0)
        monitor.check()
        self.assertEqual(beats, [])

    def test_invalid_thresholds_rejected(self) -> None:
        bus = EventBus()
        with self.assertRaises(ValueError):
            StreamingMonitor(bus, silence_threshold_sec=0.0)
        with self.assertRaises(ValueError):
            StreamingMonitor(bus, progress_beat_interval_sec=-1.0)


if __name__ == "__main__":
    unittest.main()
