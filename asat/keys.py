"""Key events and modifier vocabulary.

This module defines the abstract Key value object that the rest of
ASAT uses to describe a keystroke. It does not read keys from the
terminal or any operating system API: that is the job of a thin
platform adapter that lives outside this module.

Keeping the Key type abstract means the input router and all tests
can reason about keystrokes as plain data. Platform adapters (curses,
Windows console, prompt_toolkit, etc.) will be added in a later
phase; they only need to emit Key values onto the bus or hand them
directly to InputRouter.handle_key.

Common constants at the bottom of this module cover the keys the
Phase 4 default bindings use. More can be added as later phases need
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Modifier(str, Enum):
    """Keyboard modifier keys that can combine with a primary key."""

    CTRL = "ctrl"
    ALT = "alt"
    SHIFT = "shift"
    META = "meta"


@dataclass(frozen=True)
class Key:
    """A single keystroke.

    name: canonical name of the primary key. Printable characters
        use the character itself; special keys use lowercase words
        like "up", "down", "enter", "backspace", "escape", "f1".
    modifiers: the frozenset of modifiers held while the primary
        key was pressed. Empty for an unmodified key.
    char: the printable character associated with this keystroke,
        if any. None for special keys (arrows, function keys).
    """

    name: str
    modifiers: frozenset[Modifier] = field(default_factory=frozenset)
    char: str | None = None

    @classmethod
    def printable(cls, character: str) -> "Key":
        """Build a Key for a single printable character."""
        if len(character) != 1:
            raise ValueError("Printable key must be exactly one character")
        return cls(name=character, char=character)

    @classmethod
    def special(cls, name: str, *modifiers: Modifier) -> "Key":
        """Build a Key for a named special key like 'up' or 'escape'."""
        return cls(name=name.lower(), modifiers=frozenset(modifiers))

    @classmethod
    def combo(cls, character: str, *modifiers: Modifier) -> "Key":
        """Build a Key for a modified character like Ctrl+N."""
        return cls(name=character, modifiers=frozenset(modifiers), char=character)

    def is_printable(self) -> bool:
        """Return True if this keystroke should insert a character.

        A keystroke is considered printable when it has a char field
        and no modifiers other than plain Shift. Ctrl, Alt, and Meta
        all disqualify a keystroke from inline text insertion.
        """
        if self.char is None:
            return False
        disqualifying = {Modifier.CTRL, Modifier.ALT, Modifier.META}
        return not (self.modifiers & disqualifying)

    def has_modifier(self, modifier: Modifier) -> bool:
        """Return True if the given modifier is part of this keystroke."""
        return modifier in self.modifiers


UP = Key.special("up")
DOWN = Key.special("down")
LEFT = Key.special("left")
RIGHT = Key.special("right")
HOME = Key.special("home")
END = Key.special("end")
PAGE_UP = Key.special("page_up")
PAGE_DOWN = Key.special("page_down")
ENTER = Key.special("enter")
ESCAPE = Key.special("escape")
TAB = Key.special("tab")
BACKSPACE = Key.special("backspace")
DELETE = Key.special("delete")
F1 = Key.special("f1")
F2 = Key.special("f2")
