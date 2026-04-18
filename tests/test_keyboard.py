"""Unit tests for the keyboard parse helpers.

Real terminal reads are platform-dependent and painful to exercise in
a test environment, so these tests cover the pure decode helpers that
the platform adapters delegate to. The adapter classes themselves are
thin wrappers over `sys.stdin.read` / `msvcrt.getwch` and are smoke-
tested via integration with the CLI.
"""

from __future__ import annotations

import unittest

import io
from unittest import mock

from asat import keys as kc
from asat.keyboard import (
    KeyboardNotAvailable,
    PosixKeyboard,
    ScriptedKeyboard,
    WindowsKeyboard,
    decode_control_byte,
    decode_windows_special,
    parse_csi,
)
from asat.keys import Key, Modifier


class DecodeControlByteTests(unittest.TestCase):
    def test_enter_variants(self) -> None:
        self.assertEqual(decode_control_byte(0x0A), kc.ENTER)
        self.assertEqual(decode_control_byte(0x0D), kc.ENTER)

    def test_backspace_variants(self) -> None:
        self.assertEqual(decode_control_byte(0x08), kc.BACKSPACE)
        self.assertEqual(decode_control_byte(0x7F), kc.BACKSPACE)

    def test_tab(self) -> None:
        self.assertEqual(decode_control_byte(0x09), kc.TAB)

    def test_escape_returns_none_so_caller_collects_sequence(self) -> None:
        self.assertIsNone(decode_control_byte(0x1B))

    def test_ctrl_letter_range(self) -> None:
        self.assertEqual(decode_control_byte(0x0E), Key.combo("n", Modifier.CTRL))
        self.assertEqual(decode_control_byte(0x13), Key.combo("s", Modifier.CTRL))

    def test_printable_character(self) -> None:
        self.assertEqual(decode_control_byte(ord("a")), Key.printable("a"))
        self.assertEqual(decode_control_byte(ord(" ")), Key.printable(" "))


class ParseCsiTests(unittest.TestCase):
    def test_arrow_keys(self) -> None:
        self.assertEqual(parse_csi("\x1b[A"), kc.UP)
        self.assertEqual(parse_csi("\x1b[B"), kc.DOWN)
        self.assertEqual(parse_csi("\x1b[C"), kc.RIGHT)
        self.assertEqual(parse_csi("\x1b[D"), kc.LEFT)

    def test_home_and_end_variants(self) -> None:
        self.assertEqual(parse_csi("\x1b[H"), kc.HOME)
        self.assertEqual(parse_csi("\x1b[F"), kc.END)
        self.assertEqual(parse_csi("\x1b[1~"), kc.HOME)
        self.assertEqual(parse_csi("\x1b[4~"), kc.END)

    def test_page_motions(self) -> None:
        self.assertEqual(parse_csi("\x1b[5~"), kc.PAGE_UP)
        self.assertEqual(parse_csi("\x1b[6~"), kc.PAGE_DOWN)

    def test_unknown_sequence_returns_none(self) -> None:
        self.assertIsNone(parse_csi("\x1b[Z"))
        self.assertIsNone(parse_csi("hi"))


class DecodeWindowsSpecialTests(unittest.TestCase):
    def test_arrow_keys(self) -> None:
        self.assertEqual(decode_windows_special(0x48), kc.UP)
        self.assertEqual(decode_windows_special(0x50), kc.DOWN)

    def test_unknown_code_returns_none(self) -> None:
        self.assertIsNone(decode_windows_special(0xFF))


class ScriptedKeyboardTests(unittest.TestCase):
    def test_replays_then_returns_none(self) -> None:
        reader = ScriptedKeyboard(iter([kc.ENTER, Key.printable("a")]))
        self.assertEqual(reader.read_key(), kc.ENTER)
        self.assertEqual(reader.read_key(), Key.printable("a"))
        self.assertIsNone(reader.read_key())


class TTYGuardTests(unittest.TestCase):
    """Both platform adapters must refuse to construct on a non-TTY."""

    def test_posix_keyboard_raises_keyboard_not_available_when_stdin_is_not_a_tty(
        self,
    ) -> None:
        fake_stdin = io.StringIO("")  # StringIO.isatty() -> False
        with mock.patch("asat.keyboard.sys.stdin", fake_stdin):
            with self.assertRaises(KeyboardNotAvailable) as ctx:
                PosixKeyboard()
        self.assertIn("interactive terminal", str(ctx.exception))

    def test_windows_keyboard_raises_keyboard_not_available_when_stdin_is_not_a_tty(
        self,
    ) -> None:
        fake_stdin = io.StringIO("")
        fake_msvcrt = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"msvcrt": fake_msvcrt}):
            with mock.patch("asat.keyboard.sys.stdin", fake_stdin):
                with self.assertRaises(KeyboardNotAvailable) as ctx:
                    WindowsKeyboard()
        self.assertIn("interactive Windows console", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
