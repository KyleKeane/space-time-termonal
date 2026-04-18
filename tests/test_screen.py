"""Unit tests for the VirtualScreen."""

from __future__ import annotations

import unittest

from asat.ansi import AnsiParser
from asat.screen import ATTR_BOLD, ATTR_REVERSE, VirtualScreen


def _apply(stream: str, rows: int = 5, cols: int = 20) -> VirtualScreen:
    """Tokenize a stream and apply it to a fresh screen."""
    screen = VirtualScreen(rows=rows, cols=cols)
    screen.apply_all(AnsiParser().feed(stream))
    return screen


class TextAndCursorTests(unittest.TestCase):

    def test_plain_text_fills_first_row_and_advances_cursor(self) -> None:
        screen = _apply("hello")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "hello")
        self.assertEqual(snapshot.cursor_col, 5)
        self.assertEqual(snapshot.cursor_row, 0)

    def test_newline_moves_cursor_down(self) -> None:
        screen = _apply("hi\nby")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "hi")
        self.assertEqual(snapshot.text_rows()[1][2:4], "by")
        self.assertEqual(snapshot.cursor_row, 1)

    def test_carriage_return_moves_to_column_zero(self) -> None:
        screen = _apply("abc\rX")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "Xbc")

    def test_backspace_moves_cursor_left(self) -> None:
        screen = _apply("ab\bc")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "ac")


class CursorCommandTests(unittest.TestCase):

    def test_cup_positions_cursor_absolutely(self) -> None:
        screen = _apply("\x1b[3;5HX", rows=6, cols=20)
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.cursor_row, 2)
        self.assertEqual(snapshot.cursor_col, 5)
        self.assertEqual(snapshot.rows[2][4].char, "X")

    def test_cuu_cub_combine_for_in_place_redraw(self) -> None:
        screen = _apply("hello\x1b[3D" + "__")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "he__o")

    def test_cha_sets_absolute_column(self) -> None:
        screen = _apply("abcdef\x1b[3G*")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "ab*def")


class EraseTests(unittest.TestCase):

    def test_erase_to_end_of_line_clears_from_cursor(self) -> None:
        screen = _apply("abcdef\x1b[3D\x1b[K")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "abc")

    def test_erase_entire_line_clears_full_row(self) -> None:
        screen = _apply("abcdef\x1b[2K")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows()[0], "")

    def test_erase_display_full_resets_grid(self) -> None:
        screen = _apply("line1\nline2\x1b[2J")
        snapshot = screen.snapshot()
        self.assertEqual(snapshot.text_rows(), tuple([""] * 5))


class AttributeTests(unittest.TestCase):

    def test_sgr_reverse_marks_cells(self) -> None:
        screen = _apply("a\x1b[7mbc\x1b[0md")
        snapshot = screen.snapshot()
        row_attrs = snapshot.row_attrs(0)
        self.assertNotIn(ATTR_REVERSE, row_attrs[0])
        self.assertIn(ATTR_REVERSE, row_attrs[1])
        self.assertIn(ATTR_REVERSE, row_attrs[2])
        self.assertNotIn(ATTR_REVERSE, row_attrs[3])

    def test_sgr_bold_and_reset_on_zero(self) -> None:
        screen = _apply("\x1b[1mX\x1b[0mY")
        snapshot = screen.snapshot()
        row_attrs = snapshot.row_attrs(0)
        self.assertIn(ATTR_BOLD, row_attrs[0])
        self.assertNotIn(ATTR_BOLD, row_attrs[1])

    def test_sgr_without_params_resets_attributes(self) -> None:
        screen = _apply("\x1b[1mX\x1b[mY")
        snapshot = screen.snapshot()
        self.assertNotIn(ATTR_BOLD, snapshot.row_attrs(0)[1])


class ResetAndSnapshotTests(unittest.TestCase):

    def test_reset_returns_to_clean_state(self) -> None:
        screen = _apply("hello")
        screen.reset()
        snapshot = screen.snapshot()
        self.assertEqual((snapshot.cursor_row, snapshot.cursor_col), (0, 0))
        self.assertEqual(snapshot.text_rows()[0], "")

    def test_snapshot_is_immutable_view(self) -> None:
        screen = VirtualScreen(rows=3, cols=5)
        screen.apply_all(AnsiParser().feed("hi"))
        snapshot = screen.snapshot()
        screen.apply_all(AnsiParser().feed("xy"))
        self.assertEqual(snapshot.text_rows()[0], "hi")


if __name__ == "__main__":
    unittest.main()
