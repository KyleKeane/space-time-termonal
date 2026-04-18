"""Unit tests for TuiBridge."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.tui_bridge import TuiBridge


class _Recorder:
    """Capture every event on a bus so tests can assert ordering."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [event for event in self.events if event.event_type == event_type]


class ScreenUpdateTests(unittest.TestCase):

    def test_feed_publishes_screen_updated_with_rows(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=4, cols=10)
        bridge.feed("hello")
        screen_events = events.of(EventType.SCREEN_UPDATED)
        self.assertEqual(len(screen_events), 1)
        self.assertEqual(screen_events[0].payload["cell_id"], "c1")
        self.assertEqual(screen_events[0].payload["rows"][0], "hello")

    def test_empty_feed_emits_no_events(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1")
        bridge.feed("")
        self.assertEqual(events.events, [])


class MenuLifecycleTests(unittest.TestCase):

    def _reverse_video_menu(self) -> str:
        return (
            "\x1b[2J\x1b[1;1H"
            "apple\r\n"
            "\x1b[7mbanana\x1b[0m\r\n"
            "cherry\r\n"
        )

    def test_first_detection_publishes_detected_event(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed(self._reverse_video_menu())
        detected = events.of(EventType.INTERACTIVE_MENU_DETECTED)
        self.assertEqual(len(detected), 1)
        payload = detected[0].payload
        self.assertEqual(payload["selected_text"], "banana")
        self.assertEqual(payload["selected_index"], 1)
        self.assertEqual(payload["detection"], "reverse_video")
        self.assertEqual(len(payload["items"]), 3)

    def test_selection_change_publishes_updated_event(self) -> None:
        bus = EventBus()
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed(self._reverse_video_menu())
        events = _Recorder(bus)
        redraw = (
            "\x1b[2J\x1b[1;1H"
            "apple\r\n"
            "banana\r\n"
            "\x1b[7mcherry\x1b[0m\r\n"
        )
        bridge.feed(redraw)
        updated = events.of(EventType.INTERACTIVE_MENU_UPDATED)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].payload["selected_text"], "cherry")

    def test_menu_disappearing_publishes_cleared(self) -> None:
        bus = EventBus()
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed(self._reverse_video_menu())
        events = _Recorder(bus)
        bridge.feed("\x1b[2J\x1b[1;1Hno menu here\r\n")
        cleared = events.of(EventType.INTERACTIVE_MENU_CLEARED)
        self.assertEqual(len(cleared), 1)
        self.assertEqual(cleared[0].payload["cell_id"], "c1")

    def test_stable_menu_does_not_emit_updated(self) -> None:
        bus = EventBus()
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed(self._reverse_video_menu())
        events = _Recorder(bus)
        bridge.feed(self._reverse_video_menu())
        self.assertEqual(events.of(EventType.INTERACTIVE_MENU_UPDATED), [])


class SplitChunkTests(unittest.TestCase):

    def test_csi_split_across_feeds_still_produces_menu(self) -> None:
        bus = EventBus()
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        parts = [
            "\x1b[2J",
            "\x1b[1;1H",
            "one\r\n",
            "\x1b[7mtwo",
            "\x1b[0m\r\n",
            "three\r\n",
        ]
        for part in parts:
            bridge.feed(part)
        self.assertIsNotNone(bridge.current_menu)
        assert bridge.current_menu is not None
        self.assertEqual(bridge.current_menu.selected_text, "two")


class AnsiEventTests(unittest.TestCase):

    def test_cursor_move_publishes_ansi_cursor_moved(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed("\x1b[3;5H")
        moves = events.of(EventType.ANSI_CURSOR_MOVED)
        self.assertEqual(len(moves), 1)
        payload = moves[0].payload
        self.assertEqual(payload["cell_id"], "c1")
        self.assertEqual(payload["reason"], "absolute")
        self.assertEqual(payload["new_row"], 2)
        self.assertEqual(payload["new_col"], 4)
        self.assertEqual(payload["params"], [3, 5])

    def test_cursor_up_reports_reason_up(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed("\x1b[5;5H\x1b[2A")
        moves = events.of(EventType.ANSI_CURSOR_MOVED)
        self.assertEqual(moves[-1].payload["reason"], "up")
        self.assertEqual(moves[-1].payload["old_row"], 4)
        self.assertEqual(moves[-1].payload["new_row"], 2)

    def test_sgr_publishes_attrs_added_and_removed(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=4, cols=10)
        bridge.feed("\x1b[1;7m")
        sgr = events.of(EventType.ANSI_SGR_CHANGED)
        self.assertEqual(len(sgr), 1)
        payload = sgr[0].payload
        self.assertIn("bold", payload["attrs_added"])
        self.assertIn("reverse", payload["attrs_added"])
        self.assertEqual(payload["attrs_removed"], [])

        events.events.clear()
        bridge.feed("\x1b[0m")
        sgr = events.of(EventType.ANSI_SGR_CHANGED)
        self.assertEqual(len(sgr), 1)
        removed = sgr[0].payload["attrs_removed"]
        self.assertIn("bold", removed)
        self.assertIn("reverse", removed)

    def test_display_cleared_publishes_mode(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=4, cols=10)
        bridge.feed("\x1b[2J")
        cleared = events.of(EventType.ANSI_DISPLAY_CLEARED)
        self.assertEqual(len(cleared), 1)
        self.assertEqual(cleared[0].payload["mode"], 2)

    def test_line_erased_publishes_mode(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1", rows=4, cols=10)
        bridge.feed("abc\x1b[K")
        erased = events.of(EventType.ANSI_LINE_ERASED)
        self.assertEqual(len(erased), 1)
        self.assertEqual(erased[0].payload["mode"], 0)

    def test_bell_publishes_ansi_bell(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1")
        bridge.feed("\x07")
        bells = events.of(EventType.ANSI_BELL)
        self.assertEqual(len(bells), 1)
        self.assertEqual(bells[0].payload, {"cell_id": "c1"})

    def test_osc_title_classified_as_title(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1")
        bridge.feed("\x1b]0;My Title\x07")
        oscs = events.of(EventType.ANSI_OSC_RECEIVED)
        self.assertEqual(len(oscs), 1)
        self.assertEqual(oscs[0].payload["category"], "title")
        self.assertEqual(oscs[0].payload["body"], "0;My Title")

    def test_osc_hyperlink_classified_as_hyperlink(self) -> None:
        bus = EventBus()
        events = _Recorder(bus)
        bridge = TuiBridge(bus, cell_id="c1")
        bridge.feed("\x1b]8;;https://example.com\x07")
        oscs = events.of(EventType.ANSI_OSC_RECEIVED)
        self.assertEqual(len(oscs), 1)
        self.assertEqual(oscs[0].payload["category"], "hyperlink")


class ResetTests(unittest.TestCase):

    def test_reset_clears_current_menu_and_emits_cleared(self) -> None:
        bus = EventBus()
        bridge = TuiBridge(bus, cell_id="c1", rows=6, cols=20)
        bridge.feed(
            "apple\r\n"
            "\x1b[7mbanana\x1b[0m\r\n"
            "cherry\r\n"
        )
        assert bridge.current_menu is not None
        events = _Recorder(bus)
        bridge.reset()
        self.assertIsNone(bridge.current_menu)
        self.assertEqual(len(events.of(EventType.INTERACTIVE_MENU_CLEARED)), 1)


if __name__ == "__main__":
    unittest.main()
