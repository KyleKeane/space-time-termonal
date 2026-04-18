"""Unit tests for the interactive menu detector."""

from __future__ import annotations

import unittest

from asat.ansi import AnsiParser
from asat.interactive import InteractiveMenu, detect
from asat.screen import VirtualScreen


def _screen_from(stream: str, rows: int = 10, cols: int = 30) -> VirtualScreen:
    """Apply an ANSI stream to a fresh screen and return it."""
    screen = VirtualScreen(rows=rows, cols=cols)
    screen.apply_all(AnsiParser().feed(stream))
    return screen


class ReverseVideoStrategyTests(unittest.TestCase):

    def test_detects_menu_with_reverse_video_selected_row(self) -> None:
        stream = (
            "apple\r\n"
            "\x1b[7mbanana\x1b[0m\r\n"
            "cherry\r\n"
        )
        menu = detect(_screen_from(stream).snapshot())
        assert menu is not None
        self.assertEqual(menu.detection, "reverse_video")
        self.assertEqual(
            [item.text for item in menu.items],
            ["apple", "banana", "cherry"],
        )
        self.assertEqual(menu.selected_index, 1)
        self.assertEqual(menu.selected_text, "banana")

    def test_first_row_reverse_is_valid_selection(self) -> None:
        stream = (
            "\x1b[7mfirst\x1b[0m\r\n"
            "second\r\n"
        )
        menu = detect(_screen_from(stream).snapshot())
        assert menu is not None
        self.assertEqual(menu.selected_index, 0)

    def test_multiple_reverse_rows_skips_detection(self) -> None:
        stream = (
            "\x1b[7mone\x1b[0m\r\n"
            "\x1b[7mtwo\x1b[0m\r\n"
        )
        menu = detect(_screen_from(stream).snapshot())
        self.assertIsNone(menu)

    def test_reverse_on_blank_row_is_ignored(self) -> None:
        stream = "\x1b[7m   \x1b[0m\r\n"
        self.assertIsNone(detect(_screen_from(stream).snapshot()))

    def test_single_item_is_not_considered_a_menu(self) -> None:
        stream = "\x1b[7monly\x1b[0m\r\n"
        self.assertIsNone(detect(_screen_from(stream).snapshot()))


class PrefixMarkerStrategyTests(unittest.TestCase):

    def test_detects_menu_with_greater_than_marker(self) -> None:
        stream = (
            "  apple\r\n"
            "> banana\r\n"
            "  cherry\r\n"
        )
        menu = detect(_screen_from(stream).snapshot())
        assert menu is not None
        self.assertEqual(menu.detection, "prefix_marker")
        self.assertEqual(menu.selected_index, 1)
        self.assertEqual(menu.selected_text.strip(), "> banana")

    def test_detects_menu_with_arrow_marker(self) -> None:
        stream = "→ first\r\n  second\r\n"
        menu = detect(_screen_from(stream).snapshot())
        assert menu is not None
        self.assertEqual(menu.selected_index, 0)

    def test_two_marker_rows_are_ambiguous(self) -> None:
        stream = "> a\r\n> b\r\n"
        self.assertIsNone(detect(_screen_from(stream).snapshot()))

    def test_single_line_group_is_ignored(self) -> None:
        stream = "> lonely\r\n"
        self.assertIsNone(detect(_screen_from(stream).snapshot()))


class NoMenuTests(unittest.TestCase):

    def test_plain_text_returns_none(self) -> None:
        stream = "nothing special here\r\nsecond line"
        self.assertIsNone(detect(_screen_from(stream).snapshot()))

    def test_blank_screen_returns_none(self) -> None:
        self.assertIsNone(detect(VirtualScreen().snapshot()))


class MenuStructureTests(unittest.TestCase):

    def test_selected_flag_set_only_on_selected_item(self) -> None:
        stream = "\x1b[7mA\x1b[0m\r\nB\r\nC\r\n"
        menu = detect(_screen_from(stream).snapshot())
        assert menu is not None
        selected_flags = [item.selected for item in menu.items]
        self.assertEqual(selected_flags, [True, False, False])

    def test_items_track_screen_row_numbers(self) -> None:
        stream = (
            "\r\n"
            "\r\n"
            "a\r\n"
            "\x1b[7mb\x1b[0m\r\n"
            "c\r\n"
        )
        menu = detect(_screen_from(stream).snapshot())
        assert menu is not None
        self.assertEqual([item.row for item in menu.items], [2, 3, 4])


if __name__ == "__main__":
    unittest.main()
