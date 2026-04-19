"""Unit tests for OutputCursor."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.output_buffer import OutputBuffer, STDERR, STDOUT
from asat.output_cursor import ComposerMode, OutputCursor


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


class SearchComposerTests(unittest.TestCase):
    """F16: `/`-style search composer narrows matches as you type."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = _Recorder(self.bus)
        self.cursor = OutputCursor(self.bus)
        self.buffer = _buffer_with(
            "c1",
            [
                ("starting up", STDOUT),
                ("connecting to database", STDOUT),
                ("ERROR: connection refused", STDERR),
                ("retrying", STDOUT),
                ("ERROR: timeout", STDERR),
                ("giving up", STDOUT),
            ],
        )
        self.cursor.attach(self.buffer)

    def test_begin_search_on_empty_buffer_is_noop(self) -> None:
        cursor = OutputCursor(EventBus())
        cursor.attach(OutputBuffer("empty"))
        self.assertFalse(cursor.begin_search())
        self.assertIsNone(cursor.composer_mode)

    def test_composer_mode_is_composermode_enum(self) -> None:
        """F49: composer_mode returns a ComposerMode (str-equatable)."""
        self.cursor.begin_search()
        self.assertIs(self.cursor.composer_mode, ComposerMode.SEARCH)
        # Back-compat: the enum still compares equal to the legacy literal.
        self.assertEqual(self.cursor.composer_mode, "search")

    def test_extend_jumps_to_first_match_live(self) -> None:
        self.cursor.begin_search()
        for ch in "ERR":
            self.cursor.extend_composer(ch)
        self.assertEqual(self.cursor.composer_buffer, "ERR")
        # First line containing "err" (case-insensitive) is the first
        # ERROR line at index 2.
        self.assertEqual(self.cursor.line_number, 2)

    def test_search_is_case_insensitive(self) -> None:
        self.cursor.begin_search()
        for ch in "error":
            self.cursor.extend_composer(ch)
        self.assertEqual(self.cursor.search_match_count, 2)
        self.assertEqual(self.cursor.line_number, 2)

    def test_no_matches_leaves_position_untouched(self) -> None:
        self.cursor.begin_search()
        self.cursor.extend_composer("z")
        self.assertEqual(self.cursor.search_match_count, 0)
        # Cursor stayed on the line we attached to (last line).
        self.assertEqual(self.cursor.line_number, 5)

    def test_next_and_prev_cycle_matches(self) -> None:
        self.cursor.begin_search()
        for ch in "error":
            self.cursor.extend_composer(ch)
        self.cursor.commit_composer()
        self.assertIsNone(self.cursor.composer_mode)
        line = self.cursor.next_match()
        assert line is not None
        self.assertEqual(line.line_number, 4)
        line = self.cursor.next_match()
        assert line is not None
        # Wrap around back to the first match.
        self.assertEqual(line.line_number, 2)
        line = self.cursor.prev_match()
        assert line is not None
        self.assertEqual(line.line_number, 4)

    def test_next_match_without_search_is_noop(self) -> None:
        self.assertIsNone(self.cursor.next_match())

    def test_cancel_restores_starting_line(self) -> None:
        self.cursor.move_to_start()  # line 0
        self.cursor.begin_search()
        for ch in "error":
            self.cursor.extend_composer(ch)
        self.assertEqual(self.cursor.line_number, 2)
        self.cursor.cancel_composer()
        self.assertIsNone(self.cursor.composer_mode)
        self.assertEqual(self.cursor.line_number, 0)

    def test_backspace_recomputes_matches(self) -> None:
        self.cursor.begin_search()
        for ch in "giving":
            self.cursor.extend_composer(ch)
        self.assertEqual(self.cursor.line_number, 5)
        self.cursor.backspace_composer()  # "givin"
        self.assertEqual(self.cursor.search_match_count, 1)
        self.cursor.backspace_composer()  # "givi"
        self.cursor.backspace_composer()  # "giv"
        self.cursor.backspace_composer()  # "gi"
        self.assertGreaterEqual(self.cursor.search_match_count, 1)


class GotoComposerTests(unittest.TestCase):
    """F16: `g<number>` jumps directly to a 1-based line."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.cursor = OutputCursor(self.bus)
        self.buffer = _buffer_with(
            "c1",
            [(f"line-{i}", STDOUT) for i in range(10)],
        )
        self.cursor.attach(self.buffer)

    def test_goto_jumps_to_one_based_line(self) -> None:
        self.cursor.begin_goto()
        self.cursor.extend_composer("3")
        self.cursor.commit_composer()
        self.assertEqual(self.cursor.line_number, 2)  # 1-based 3 -> index 2
        self.assertIsNone(self.cursor.composer_mode)

    def test_goto_rejects_non_digits(self) -> None:
        self.cursor.begin_goto()
        self.cursor.extend_composer("a")  # ignored
        self.cursor.extend_composer("2")
        self.cursor.extend_composer("b")  # ignored
        self.assertEqual(self.cursor.composer_buffer, "2")

    def test_goto_clamps_beyond_end(self) -> None:
        self.cursor.begin_goto()
        for ch in "999":
            self.cursor.extend_composer(ch)
        self.cursor.commit_composer()
        self.assertEqual(self.cursor.line_number, 9)

    def test_goto_commit_with_empty_buffer_is_noop(self) -> None:
        start = self.cursor.line_number
        self.cursor.begin_goto()
        self.cursor.commit_composer()
        self.assertEqual(self.cursor.line_number, start)

    def test_jump_to_line_direct_api(self) -> None:
        self.cursor.jump_to_line(4)
        self.assertEqual(self.cursor.line_number, 4)


if __name__ == "__main__":
    unittest.main()
