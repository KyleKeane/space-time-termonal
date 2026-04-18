"""VirtualScreen: applies ANSI tokens to a 2D cell grid.

A TUI draws by sending a sequence of ANSI tokens: move the cursor,
write a character, set a colour attribute, clear a line, repeat. To
reason about what the user is seeing we need to replay that sequence
into a grid that remembers the resulting cells. VirtualScreen is that
grid.

It is deliberately small: it handles the tokens most commonly used by
interactive menus and progress bars, and ignores the rest. In
particular it implements:

    Plain text output (advances cursor, wraps at right margin).
    CR / LF (\\r moves to column 0; \\n moves down one row).
    BS (\\b moves cursor left).
    CUU / CUD / CUF / CUB (ESC [ n A/B/C/D) cursor moves.
    CUP (ESC [ r;c H or ESC [ r;c f) absolute cursor position.
    ED  (ESC [ n J) erase in display, modes 0/1/2.
    EL  (ESC [ n K) erase in line, modes 0/1/2.
    SGR (ESC [ ... m) for reverse video and the common bold/bright
        attributes that menu detectors look for. Unknown SGR
        parameters are tracked verbatim so downstream heuristics can
        key on them without losing information.

Cursor positions are stored zero-based internally; ANSI addresses
them one-based, which is handled in the CSI handlers.

VirtualScreen exposes a snapshot() method that returns an immutable
ScreenSnapshot: text per row (with trailing spaces trimmed), the
attribute set for each cell on each row, and the cursor position.
The menu detector operates on snapshots so it never shares mutable
state with the screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from asat.ansi import (
    CSIToken,
    ControlToken,
    EscapeToken,
    OSCToken,
    TextToken,
    Token,
)


DEFAULT_COLS = 80
DEFAULT_ROWS = 24

ATTR_REVERSE = "reverse"
ATTR_BOLD = "bold"
ATTR_UNDERLINE = "underline"
ATTR_DIM = "dim"


@dataclass(frozen=True)
class Cell:
    """One character cell on the virtual screen."""

    char: str = " "
    attrs: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ScreenSnapshot:
    """Immutable view of the virtual screen at a point in time."""

    rows: tuple[tuple[Cell, ...], ...]
    cursor_row: int
    cursor_col: int
    cols: int

    def text_rows(self) -> tuple[str, ...]:
        """Return each row as a string with trailing spaces trimmed."""
        return tuple("".join(cell.char for cell in row).rstrip() for row in self.rows)

    def row_attrs(self, row_index: int) -> tuple[frozenset[str], ...]:
        """Return the per-cell attribute set for the given row."""
        return tuple(cell.attrs for cell in self.rows[row_index])


@dataclass
class VirtualScreen:
    """Mutable 2D grid updated by applying ANSI tokens."""

    rows: int = DEFAULT_ROWS
    cols: int = DEFAULT_COLS
    _grid: list[list[Cell]] = field(default_factory=list)
    _cursor_row: int = 0
    _cursor_col: int = 0
    _attrs: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Allocate a fresh blank grid if one was not provided."""
        if not self._grid:
            self._grid = [self._blank_row() for _ in range(self.rows)]

    @property
    def cursor(self) -> tuple[int, int]:
        """Return (row, col) of the current cursor position."""
        return self._cursor_row, self._cursor_col

    def apply(self, token: Token) -> None:
        """Mutate the screen by applying a single token."""
        if isinstance(token, TextToken):
            self._write_text(token.text)
        elif isinstance(token, ControlToken):
            self._apply_control(token.char)
        elif isinstance(token, CSIToken):
            self._apply_csi(token)
        elif isinstance(token, EscapeToken):
            self._apply_escape(token)
        elif isinstance(token, OSCToken):
            pass

    def apply_all(self, tokens: list[Token]) -> None:
        """Apply each token in order."""
        for token in tokens:
            self.apply(token)

    def snapshot(self) -> ScreenSnapshot:
        """Return an immutable copy of the current grid and cursor."""
        rows = tuple(tuple(row) for row in self._grid)
        return ScreenSnapshot(
            rows=rows,
            cursor_row=self._cursor_row,
            cursor_col=self._cursor_col,
            cols=self.cols,
        )

    def reset(self) -> None:
        """Clear the grid, reset attributes, and park the cursor at home."""
        self._grid = [self._blank_row() for _ in range(self.rows)]
        self._cursor_row = 0
        self._cursor_col = 0
        self._attrs = frozenset()

    def _blank_row(self) -> list[Cell]:
        """Return a fresh row filled with blank cells."""
        return [Cell() for _ in range(self.cols)]

    def _write_text(self, text: str) -> None:
        """Write a run of printable characters at the cursor."""
        for char in text:
            if self._cursor_col >= self.cols:
                self._cursor_col = self.cols - 1
            row = self._grid[self._cursor_row]
            row[self._cursor_col] = Cell(char=char, attrs=self._attrs)
            self._cursor_col += 1
            if self._cursor_col >= self.cols:
                self._cursor_col = self.cols - 1

    def _apply_control(self, char: str) -> None:
        """Handle CR, LF, BS, TAB, BEL."""
        if char == "\r":
            self._cursor_col = 0
        elif char == "\n":
            if self._cursor_row < self.rows - 1:
                self._cursor_row += 1
        elif char == "\b":
            if self._cursor_col > 0:
                self._cursor_col -= 1
        elif char == "\t":
            next_stop = ((self._cursor_col // 8) + 1) * 8
            self._cursor_col = min(next_stop, self.cols - 1)

    def _apply_csi(self, token: CSIToken) -> None:
        """Dispatch a CSI token to the matching handler."""
        handler = _CSI_HANDLERS.get(token.final)
        if handler is None:
            return
        handler(self, token)

    def _apply_escape(self, _token: EscapeToken) -> None:
        """Bare ESC commands are ignored for now."""

    def _clamp_cursor(self) -> None:
        """Keep the cursor inside the screen bounds."""
        self._cursor_row = max(0, min(self._cursor_row, self.rows - 1))
        self._cursor_col = max(0, min(self._cursor_col, self.cols - 1))


def _param_or(token: CSIToken, index: int, default: int) -> int:
    """Return the param at index or default when missing or -1."""
    if index >= len(token.params):
        return default
    value = token.params[index]
    return default if value < 0 else value


def _cursor_up(screen: VirtualScreen, token: CSIToken) -> None:
    """CUU: move cursor up N rows, clamping at top."""
    screen._cursor_row -= max(1, _param_or(token, 0, 1))
    screen._clamp_cursor()


def _cursor_down(screen: VirtualScreen, token: CSIToken) -> None:
    """CUD: move cursor down N rows, clamping at bottom."""
    screen._cursor_row += max(1, _param_or(token, 0, 1))
    screen._clamp_cursor()


def _cursor_forward(screen: VirtualScreen, token: CSIToken) -> None:
    """CUF: move cursor right N columns."""
    screen._cursor_col += max(1, _param_or(token, 0, 1))
    screen._clamp_cursor()


def _cursor_back(screen: VirtualScreen, token: CSIToken) -> None:
    """CUB: move cursor left N columns."""
    screen._cursor_col -= max(1, _param_or(token, 0, 1))
    screen._clamp_cursor()


def _cursor_position(screen: VirtualScreen, token: CSIToken) -> None:
    """CUP: absolute cursor position (one-based)."""
    row = max(1, _param_or(token, 0, 1)) - 1
    col = max(1, _param_or(token, 1, 1)) - 1
    screen._cursor_row = row
    screen._cursor_col = col
    screen._clamp_cursor()


def _erase_in_display(screen: VirtualScreen, token: CSIToken) -> None:
    """ED: erase in display. 0=after cursor, 1=before, 2=all."""
    mode = _param_or(token, 0, 0)
    if mode == 2:
        screen.reset()
        return
    if mode == 0:
        _erase_line_from_cursor(screen)
        for row_index in range(screen._cursor_row + 1, screen.rows):
            screen._grid[row_index] = screen._blank_row()
    elif mode == 1:
        _erase_line_to_cursor(screen)
        for row_index in range(0, screen._cursor_row):
            screen._grid[row_index] = screen._blank_row()


def _erase_in_line(screen: VirtualScreen, token: CSIToken) -> None:
    """EL: erase in line. 0=to end, 1=to start, 2=entire row."""
    mode = _param_or(token, 0, 0)
    if mode == 0:
        _erase_line_from_cursor(screen)
    elif mode == 1:
        _erase_line_to_cursor(screen)
    elif mode == 2:
        screen._grid[screen._cursor_row] = screen._blank_row()


def _erase_line_from_cursor(screen: VirtualScreen) -> None:
    """Blank cells from the cursor column to the end of the row."""
    row = screen._grid[screen._cursor_row]
    for col in range(screen._cursor_col, screen.cols):
        row[col] = Cell()


def _erase_line_to_cursor(screen: VirtualScreen) -> None:
    """Blank cells from column 0 to and including the cursor column."""
    row = screen._grid[screen._cursor_row]
    for col in range(0, screen._cursor_col + 1):
        row[col] = Cell()


def _sgr(screen: VirtualScreen, token: CSIToken) -> None:
    """SGR: apply Select Graphic Rendition updates to current attributes."""
    params = token.params or (0,)
    attrs = set(screen._attrs)
    for raw in params:
        code = 0 if raw < 0 else raw
        if code == 0:
            attrs.clear()
        elif code == 1:
            attrs.add(ATTR_BOLD)
        elif code == 2:
            attrs.add(ATTR_DIM)
        elif code == 4:
            attrs.add(ATTR_UNDERLINE)
        elif code == 7:
            attrs.add(ATTR_REVERSE)
        elif code == 22:
            attrs.discard(ATTR_BOLD)
            attrs.discard(ATTR_DIM)
        elif code == 24:
            attrs.discard(ATTR_UNDERLINE)
        elif code == 27:
            attrs.discard(ATTR_REVERSE)
    screen._attrs = frozenset(attrs)


def _set_column(screen: VirtualScreen, token: CSIToken) -> None:
    """CHA: move cursor to absolute column (one-based)."""
    col = max(1, _param_or(token, 0, 1)) - 1
    screen._cursor_col = col
    screen._clamp_cursor()


_CSI_HANDLERS = {
    "A": _cursor_up,
    "B": _cursor_down,
    "C": _cursor_forward,
    "D": _cursor_back,
    "G": _set_column,
    "H": _cursor_position,
    "f": _cursor_position,
    "J": _erase_in_display,
    "K": _erase_in_line,
    "m": _sgr,
}
