"""Unit tests for OutputPlaybackDriver (F24)."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.output_buffer import OutputBuffer, STDOUT
from asat.output_cursor import OutputCursor
from asat.output_playback import OutputPlaybackDriver


class _VirtualClock:
    """Deterministic monotonic clock for driving `step()` by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def tick(self, delta: float) -> float:
        self.now += delta
        return self.now

    def __call__(self) -> float:
        return self.now


class _EventLog:
    """Collects playback lifecycle events for assertions."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(EventType.OUTPUT_PLAYBACK_STARTED, self.events.append)
        bus.subscribe(EventType.OUTPUT_PLAYBACK_STOPPED, self.events.append)
        self.focused: list[Event] = []
        bus.subscribe(EventType.OUTPUT_LINE_FOCUSED, self.focused.append)


def _populated_cursor(bus: EventBus, lines: int = 4) -> OutputCursor:
    buffer = OutputBuffer(cell_id="c1")
    for i in range(lines):
        buffer.append(f"line {i}", stream=STDOUT)
    cursor = OutputCursor(bus)
    cursor.attach(buffer)
    # attach() snaps to the last line; reset to the top so playback
    # has somewhere to advance to.
    cursor.move_to_start()
    return cursor


class StartStopTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.log = _EventLog(self.bus)
        self.clock = _VirtualClock()
        self.cursor = _populated_cursor(self.bus)
        self.driver = OutputPlaybackDriver(
            self.bus, self.cursor, interval_sec=1.0, clock=self.clock
        )

    def test_start_publishes_started_event(self) -> None:
        self.assertTrue(self.driver.start(cell_id="c1"))
        self.assertTrue(self.driver.active)
        started = [
            e for e in self.log.events
            if e.event_type is EventType.OUTPUT_PLAYBACK_STARTED
        ]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].payload["cell_id"], "c1")
        self.assertEqual(started[0].payload["interval_sec"], 1.0)
        self.assertEqual(started[0].source, "output_playback")

    def test_start_while_active_returns_false(self) -> None:
        self.assertTrue(self.driver.start())
        self.assertFalse(self.driver.start())

    def test_start_with_detached_cursor_returns_false(self) -> None:
        self.cursor.detach()
        self.assertFalse(self.driver.start())
        self.assertFalse(self.driver.active)

    def test_start_at_bottom_returns_false(self) -> None:
        # OutputCursor.attach snaps to the last line; without the
        # move_to_start in _populated_cursor, we'd be on the last line
        # already. Force that state here.
        buffer = OutputBuffer(cell_id="c1")
        buffer.append("only line", stream=STDOUT)
        cursor = OutputCursor(self.bus)
        cursor.attach(buffer)
        driver = OutputPlaybackDriver(
            self.bus, cursor, interval_sec=1.0, clock=self.clock
        )
        self.assertFalse(driver.start())

    def test_stop_publishes_stopped_event(self) -> None:
        self.driver.start(cell_id="c1")
        self.assertTrue(self.driver.stop(reason="cancelled"))
        stopped = [
            e for e in self.log.events
            if e.event_type is EventType.OUTPUT_PLAYBACK_STOPPED
        ]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0].payload["reason"], "cancelled")
        self.assertEqual(stopped[0].payload["cell_id"], "c1")
        self.assertFalse(self.driver.active)

    def test_stop_when_inactive_is_noop(self) -> None:
        self.assertFalse(self.driver.stop())
        stopped = [
            e for e in self.log.events
            if e.event_type is EventType.OUTPUT_PLAYBACK_STOPPED
        ]
        self.assertEqual(stopped, [])

    def test_construction_rejects_non_positive_interval(self) -> None:
        with self.assertRaises(ValueError):
            OutputPlaybackDriver(self.bus, self.cursor, interval_sec=0)
        with self.assertRaises(ValueError):
            OutputPlaybackDriver(self.bus, self.cursor, interval_sec=-1.0)


class StepAdvanceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.log = _EventLog(self.bus)
        self.clock = _VirtualClock()
        self.cursor = _populated_cursor(self.bus, lines=4)
        self.driver = OutputPlaybackDriver(
            self.bus, self.cursor, interval_sec=1.0, clock=self.clock
        )

    def test_step_before_interval_is_noop(self) -> None:
        self.driver.start()
        start_line = self.cursor.line_number
        self.clock.tick(0.5)
        self.assertFalse(self.driver.step())
        self.assertEqual(self.cursor.line_number, start_line)

    def test_step_at_interval_advances_one_line(self) -> None:
        self.driver.start()
        start_line = self.cursor.line_number
        self.clock.tick(1.0)
        self.assertTrue(self.driver.step())
        self.assertEqual(self.cursor.line_number, start_line + 1)

    def test_multiple_steps_keep_advancing(self) -> None:
        self.driver.start()
        for i in range(1, 4):
            self.clock.tick(1.0)
            self.driver.step()
            self.assertEqual(self.cursor.line_number, i)

    def test_step_when_inactive_is_noop(self) -> None:
        self.clock.tick(5.0)
        self.assertFalse(self.driver.step())

    def test_reaching_end_stops_with_reason_end(self) -> None:
        # buffer has 4 lines (indices 0..3); starting at 0 we need 3
        # advances to reach the last line.
        self.driver.start()
        for _ in range(3):
            self.clock.tick(1.0)
            self.driver.step()
        self.assertFalse(self.driver.active)
        stopped = [
            e for e in self.log.events
            if e.event_type is EventType.OUTPUT_PLAYBACK_STOPPED
        ]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0].payload["reason"], "end")


class CloseTests(unittest.TestCase):

    def test_close_stops_active_playback(self) -> None:
        bus = EventBus()
        log = _EventLog(bus)
        cursor = _populated_cursor(bus)
        driver = OutputPlaybackDriver(bus, cursor, interval_sec=1.0)
        driver.start()
        driver.close()
        self.assertFalse(driver.active)
        stopped = [
            e for e in log.events
            if e.event_type is EventType.OUTPUT_PLAYBACK_STOPPED
        ]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0].payload["reason"], "cancelled")


if __name__ == "__main__":
    unittest.main()
