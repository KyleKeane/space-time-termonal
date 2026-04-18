"""Keyboard adapters: read OS keystrokes and emit Key values.

This is the single place that touches terminal input APIs. Keeping
every `msvcrt` / `termios` / `tty` call in here means the rest of
ASAT stays I/O-free and unit-testable without a real keyboard.

Two adapters ship today:

- `WindowsKeyboard` uses the `msvcrt.getwch` blocking read and decodes
  the two-byte prefix (`\\x00` or `\\xe0`) that arrives for arrow keys,
  function keys, and Home/End.
- `PosixKeyboard` puts stdin into cbreak mode via `termios` + `tty`
  and parses the CSI escape sequences that xterm-family terminals
  emit for the same keys.

Both read byte-by-byte rather than line-by-line so the driver can
react to every keystroke — that is the only way to hear `insert_character`
narrations in real time.

A pure helper `parse_csi(sequence)` is exposed for tests: it turns an
already-collected escape sequence like `"\\x1b[A"` into the matching
`Key` value without touching any file descriptor.

`pick_default()` returns the right adapter for the current platform.
Tests and alternative front-ends should construct an adapter directly,
or pass a pre-built iterable of `Key` values to the driver.
"""

from __future__ import annotations

import sys
from typing import Iterator, Optional, Protocol

from asat import keys as kc
from asat.keys import Key, Modifier


_CTRL_A_ORD = 0x01
_CTRL_Z_ORD = 0x1A
_BACKSPACE_ORDS = (0x7F, 0x08)
_ENTER_ORDS = (0x0A, 0x0D)
_ESC_ORD = 0x1B
_TAB_ORD = 0x09

_CSI_FINAL_TO_KEY: dict[str, Key] = {
    "A": kc.UP,
    "B": kc.DOWN,
    "C": kc.RIGHT,
    "D": kc.LEFT,
    "H": kc.HOME,
    "F": kc.END,
}

_CSI_TILDE_TO_KEY: dict[str, Key] = {
    "1": kc.HOME,
    "4": kc.END,
    "5": kc.PAGE_UP,
    "6": kc.PAGE_DOWN,
    "7": kc.HOME,
    "8": kc.END,
    "3": kc.DELETE,
}

_WINDOWS_PREFIX_ORDS = (0x00, 0xE0)

_WINDOWS_SPECIAL: dict[int, Key] = {
    0x48: kc.UP,
    0x50: kc.DOWN,
    0x4B: kc.LEFT,
    0x4D: kc.RIGHT,
    0x47: kc.HOME,
    0x4F: kc.END,
    0x49: kc.PAGE_UP,
    0x51: kc.PAGE_DOWN,
    0x53: kc.DELETE,
}


class KeyboardReader(Protocol):
    """Produces one `Key` value per real keystroke."""

    def read_key(self) -> Optional[Key]:
        """Block until a keystroke arrives and return the decoded Key."""
        ...

    def close(self) -> None:
        """Release any terminal state the reader acquired."""
        ...


def decode_control_byte(code: int) -> Optional[Key]:
    """Turn a single stdin byte into a Key for non-escape cases.

    Returns None if the byte opens a multi-byte sequence (ESC, the
    Windows prefix bytes) — callers must collect more input.
    """
    if code in _ENTER_ORDS:
        return kc.ENTER
    if code in _BACKSPACE_ORDS:
        return kc.BACKSPACE
    if code == _TAB_ORD:
        return kc.TAB
    if code == _ESC_ORD:
        return None
    if _CTRL_A_ORD <= code <= _CTRL_Z_ORD:
        letter = chr(code + ord("a") - 1)
        return Key.combo(letter, Modifier.CTRL)
    try:
        character = chr(code)
    except ValueError:
        return None
    if character.isprintable():
        return Key.printable(character)
    return None


def parse_csi(sequence: str) -> Optional[Key]:
    """Translate a CSI escape sequence into a Key, or None if unknown.

    `sequence` is the full collected string including the leading ESC
    and either `[` or `O`. Examples: `"\\x1b[A"` for Up, `"\\x1b[5~"`
    for PageUp, `"\\x1b[1;5A"` for Ctrl+Up (Ctrl modifier is dropped
    since our default bindings do not use it for arrows).
    """
    if len(sequence) < 3 or sequence[0] != "\x1b":
        return None
    if sequence[1] not in ("[", "O"):
        return None
    final = sequence[-1]
    if final == "~":
        digits = sequence[2:-1].split(";", 1)[0]
        return _CSI_TILDE_TO_KEY.get(digits)
    return _CSI_FINAL_TO_KEY.get(final)


def decode_windows_special(code: int) -> Optional[Key]:
    """Translate a Windows two-byte second byte into a Key, or None."""
    return _WINDOWS_SPECIAL.get(code)


class KeyboardNotAvailable(RuntimeError):
    """Raised when the process has no usable interactive keyboard.

    The most common trigger is a non-TTY stdin: someone runs
    `echo :quit | python -m asat`, or launches from a sandbox that
    pipes stdin, or runs under a CI harness. In those cases the
    Posix / Windows adapters cannot put the terminal into cbreak
    mode and would otherwise surface a raw `termios.error` or an
    IO error as an unexplained traceback.
    """


class PosixKeyboard:
    """Read keystrokes from stdin in cbreak mode and decode them."""

    def __init__(self) -> None:
        """Save the current terminal attributes and enter cbreak mode."""
        import termios
        import tty

        if not sys.stdin.isatty():
            raise KeyboardNotAvailable(
                "ASAT needs an interactive terminal (a TTY). "
                "stdin is not a TTY, so keystrokes cannot be read. "
                "Launch from a real terminal, or use ScriptedKeyboard "
                "in tests."
            )
        self._termios = termios
        self._fd = sys.stdin.fileno()
        self._original = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)

    def read_key(self) -> Optional[Key]:
        """Read one keystroke, collecting an ESC sequence if needed."""
        byte = sys.stdin.read(1)
        if not byte:
            return None
        code = ord(byte)
        if code != _ESC_ORD:
            return decode_control_byte(code)
        following = sys.stdin.read(1)
        if not following or following not in ("[", "O"):
            return kc.ESCAPE
        sequence = "\x1b" + following
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                return None
            sequence += ch
            if ch.isalpha() or ch == "~":
                break
        return parse_csi(sequence)

    def close(self) -> None:
        """Restore the terminal attributes saved at construction time."""
        self._termios.tcsetattr(
            self._fd,
            self._termios.TCSADRAIN,
            self._original,
        )


class WindowsKeyboard:
    """Read keystrokes from the Windows console via msvcrt."""

    def __init__(self) -> None:
        """Import msvcrt; there is no terminal state to save."""
        import msvcrt

        if not sys.stdin.isatty():
            raise KeyboardNotAvailable(
                "ASAT needs an interactive Windows console. "
                "stdin is not a TTY, so keystrokes cannot be read. "
                "Launch from cmd, PowerShell, or Windows Terminal."
            )
        self._msvcrt = msvcrt

    def read_key(self) -> Optional[Key]:
        """Read one wide character, collecting the prefix pair for specials."""
        char = self._msvcrt.getwch()
        if not char:
            return None
        code = ord(char)
        if code in _WINDOWS_PREFIX_ORDS:
            follow = self._msvcrt.getwch()
            if not follow:
                return None
            return decode_windows_special(ord(follow))
        return decode_control_byte(code)

    def close(self) -> None:
        """No terminal state to restore."""


class ScriptedKeyboard:
    """A reader that replays a fixed sequence of Keys. For tests and demos."""

    def __init__(self, keys: Iterator[Key]) -> None:
        """Remember the iterator; each read_key consumes the next item."""
        self._keys = iter(keys)

    def read_key(self) -> Optional[Key]:
        """Return the next Key or None when the script is exhausted."""
        return next(self._keys, None)

    def close(self) -> None:
        """No-op. Present to satisfy the KeyboardReader protocol."""


def pick_default() -> KeyboardReader:
    """Return the keyboard adapter that matches the current platform."""
    if sys.platform.startswith("win"):
        return WindowsKeyboard()
    return PosixKeyboard()
