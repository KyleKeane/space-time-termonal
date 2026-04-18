"""Unit tests for the Key value object."""

from __future__ import annotations

import unittest

from asat.keys import (
    BACKSPACE,
    DOWN,
    ENTER,
    ESCAPE,
    Key,
    Modifier,
    UP,
)


class KeyConstructionTests(unittest.TestCase):

    def test_printable_sets_char_and_name(self) -> None:
        key = Key.printable("a")
        self.assertEqual(key.name, "a")
        self.assertEqual(key.char, "a")
        self.assertEqual(key.modifiers, frozenset())

    def test_printable_rejects_multi_character_input(self) -> None:
        with self.assertRaises(ValueError):
            Key.printable("ab")

    def test_special_lowercases_name_and_no_char(self) -> None:
        key = Key.special("Up")
        self.assertEqual(key.name, "up")
        self.assertIsNone(key.char)

    def test_combo_records_modifiers(self) -> None:
        key = Key.combo("n", Modifier.CTRL)
        self.assertEqual(key.char, "n")
        self.assertTrue(key.has_modifier(Modifier.CTRL))
        self.assertFalse(key.has_modifier(Modifier.ALT))

    def test_equality_and_hashing(self) -> None:
        a = Key.special("up")
        b = Key.special("up")
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))

    def test_modifier_sets_distinguish_keys(self) -> None:
        plain = Key.printable("n")
        ctrl_n = Key.combo("n", Modifier.CTRL)
        self.assertNotEqual(plain, ctrl_n)


class PrintableClassificationTests(unittest.TestCase):

    def test_printable_letter_is_printable(self) -> None:
        self.assertTrue(Key.printable("x").is_printable())

    def test_special_key_is_not_printable(self) -> None:
        self.assertFalse(UP.is_printable())
        self.assertFalse(ENTER.is_printable())

    def test_ctrl_combo_is_not_printable(self) -> None:
        self.assertFalse(Key.combo("n", Modifier.CTRL).is_printable())

    def test_alt_combo_is_not_printable(self) -> None:
        self.assertFalse(Key.combo("x", Modifier.ALT).is_printable())

    def test_shift_alone_remains_printable(self) -> None:
        shifted = Key(name="A", modifiers=frozenset({Modifier.SHIFT}), char="A")
        self.assertTrue(shifted.is_printable())


class CommonKeyConstantTests(unittest.TestCase):

    def test_arrow_and_control_keys_are_named(self) -> None:
        self.assertEqual(UP.name, "up")
        self.assertEqual(DOWN.name, "down")
        self.assertEqual(ENTER.name, "enter")
        self.assertEqual(ESCAPE.name, "escape")
        self.assertEqual(BACKSPACE.name, "backspace")


if __name__ == "__main__":
    unittest.main()
