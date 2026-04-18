"""TuiBridge: glue between a raw program stream and the menu detector.

The parser, virtual screen, and menu detector are each pure-function
style components. TuiBridge composes them so a caller can push raw
text from a running TUI in and get interactive-menu events out.

A bridge belongs to a single cell and a single process stream. It
holds:

    An AnsiParser that tolerates chunks split mid-sequence.
    A VirtualScreen that replays tokens into a grid.
    The most recently detected InteractiveMenu (or None).

Each feed() call:

    1. Tokenizes the new text.
    2. Applies the tokens to the screen.
    3. Runs the menu detector on the fresh snapshot.
    4. Emits SCREEN_UPDATED plus a menu lifecycle event when the menu
       state transitions.

The bridge is deliberately not wired to any bus event by default.
Line-level OUTPUT_CHUNK events strip the sub-line detail that TUIs
depend on (cursor moves, in-place redraws), so a future phase will
introduce a raw-byte stream that can feed the bridge. Until then the
bridge is driven by callers (or tests) directly through feed().
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from asat.ansi import (
    AnsiParser,
    BEL,
    CSIToken,
    ControlToken,
    OSCToken,
    Token,
)
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.interactive import InteractiveMenu, detect
from asat.screen import DEFAULT_COLS, DEFAULT_ROWS, VirtualScreen


# CSI finals that move the cursor. Mapped to the "reason" string we
# include in the ANSI_CURSOR_MOVED payload so bindings can filter on
# direction without re-parsing the raw sequence.
_CURSOR_MOVE_FINALS: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "forward",
    "D": "back",
    "E": "next_line",
    "F": "previous_line",
    "G": "column",
    "H": "absolute",
    "f": "absolute",
}


class TuiBridge:
    """Turns a raw TUI stream into menu-lifecycle events."""

    SOURCE = "tui_bridge"

    def __init__(
        self,
        bus: EventBus,
        cell_id: str,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
    ) -> None:
        """Create a bridge bound to a cell with a fresh parser and screen."""
        self._bus = bus
        self._cell_id = cell_id
        self._parser = AnsiParser()
        self._screen = VirtualScreen(rows=rows, cols=cols)
        self._menu: Optional[InteractiveMenu] = None

    @property
    def cell_id(self) -> str:
        """Return the cell id this bridge belongs to."""
        return self._cell_id

    @property
    def screen(self) -> VirtualScreen:
        """Return the virtual screen the bridge is maintaining."""
        return self._screen

    @property
    def current_menu(self) -> Optional[InteractiveMenu]:
        """Return the most recently detected menu, or None."""
        return self._menu

    def feed(self, data: str) -> Optional[InteractiveMenu]:
        """Consume more stream bytes and run detection.

        Returns the currently detected menu (or None). Publishes
        SCREEN_UPDATED and, on transitions, INTERACTIVE_MENU_DETECTED /
        INTERACTIVE_MENU_UPDATED / INTERACTIVE_MENU_CLEARED. Every
        ANSI-level token also publishes a fine-grained event
        (ANSI_CURSOR_MOVED, ANSI_SGR_CHANGED, ANSI_DISPLAY_CLEARED,
        ANSI_LINE_ERASED, ANSI_OSC_RECEIVED, ANSI_BELL) so SoundBank
        bindings can sonify low-level terminal behaviour.
        """
        if not data:
            return self._menu
        tokens = self._parser.feed(data)
        if not tokens:
            return self._menu
        self._apply_with_events(tokens)
        self._publish_screen_update()
        new_menu = detect(self._screen.snapshot())
        self._transition_menu(new_menu)
        return self._menu

    def finish(self) -> Optional[InteractiveMenu]:
        """Flush any buffered text and return the final menu state."""
        tokens = self._parser.finish()
        if tokens:
            self._apply_with_events(tokens)
            self._publish_screen_update()
            new_menu = detect(self._screen.snapshot())
            self._transition_menu(new_menu)
        return self._menu

    def _apply_with_events(self, tokens: list[Token]) -> None:
        """Apply each token, emitting a matching ANSI_* event per token.

        The event goes out *after* the screen mutation so payload
        fields like `cursor_row` reflect the post-apply state. Tokens
        that do not map to an ANSI event (TextToken, EscapeToken)
        still mutate the screen but emit nothing on their own.
        """
        for token in tokens:
            previous_attrs = frozenset(self._screen.attrs)
            previous_row = self._screen.cursor_row
            previous_col = self._screen.cursor_col
            self._screen.apply(token)
            self._publish_token_event(token, previous_attrs, previous_row, previous_col)

    def _publish_token_event(
        self,
        token: Token,
        previous_attrs: frozenset[str],
        previous_row: int,
        previous_col: int,
    ) -> None:
        """Emit one ANSI_* event for a token, if it maps to one."""
        if isinstance(token, ControlToken):
            if token.char == BEL:
                self._publish_ansi(EventType.ANSI_BELL, {})
            return
        if isinstance(token, CSIToken):
            self._publish_csi_event(token, previous_attrs, previous_row, previous_col)
            return
        if isinstance(token, OSCToken):
            self._publish_ansi(
                EventType.ANSI_OSC_RECEIVED,
                {"body": token.body, "category": _classify_osc(token.body)},
            )

    def _publish_csi_event(
        self,
        token: CSIToken,
        previous_attrs: frozenset[str],
        previous_row: int,
        previous_col: int,
    ) -> None:
        """Publish the ANSI_* event matching this CSI token's final byte."""
        final = token.final
        if final in _CURSOR_MOVE_FINALS:
            self._publish_ansi(
                EventType.ANSI_CURSOR_MOVED,
                {
                    "reason": _CURSOR_MOVE_FINALS[final],
                    "old_row": previous_row,
                    "old_col": previous_col,
                    "new_row": self._screen.cursor_row,
                    "new_col": self._screen.cursor_col,
                    "params": list(token.params),
                },
            )
            return
        if final == "m":
            current = frozenset(self._screen.attrs)
            self._publish_ansi(
                EventType.ANSI_SGR_CHANGED,
                {
                    "params": list(token.params),
                    "attrs_added": sorted(current - previous_attrs),
                    "attrs_removed": sorted(previous_attrs - current),
                    "current_attrs": sorted(current),
                },
            )
            return
        if final == "J":
            self._publish_ansi(
                EventType.ANSI_DISPLAY_CLEARED,
                {"mode": token.params[0] if token.params else 0},
            )
            return
        if final == "K":
            self._publish_ansi(
                EventType.ANSI_LINE_ERASED,
                {"mode": token.params[0] if token.params else 0},
            )

    def _publish_ansi(self, event_type: EventType, extra: dict) -> None:
        """Publish an ANSI_* event carrying cell_id plus event-specific fields."""
        payload: dict = {"cell_id": self._cell_id}
        payload.update(extra)
        publish_event(self._bus, event_type, payload, source=self.SOURCE)

    def reset(self) -> None:
        """Clear the screen and the detected menu; publish CLEARED if needed."""
        self._screen.reset()
        self._parser = AnsiParser()
        self._transition_menu(None)

    def _transition_menu(self, new_menu: Optional[InteractiveMenu]) -> None:
        """Compare the new menu to the previous and emit the right event."""
        previous = self._menu
        self._menu = new_menu
        if new_menu is None and previous is None:
            return
        if new_menu is not None and previous is None:
            self._publish_menu_event(EventType.INTERACTIVE_MENU_DETECTED, new_menu)
            return
        if new_menu is None and previous is not None:
            self._publish_menu_cleared()
            return
        assert new_menu is not None and previous is not None
        if not _menus_equivalent(previous, new_menu):
            self._publish_menu_event(EventType.INTERACTIVE_MENU_UPDATED, new_menu)

    def _publish_screen_update(self) -> None:
        """Publish a SCREEN_UPDATED event containing the text rows."""
        snapshot = self._screen.snapshot()
        publish_event(
            self._bus,
            EventType.SCREEN_UPDATED,
            {
                "cell_id": self._cell_id,
                "cursor_row": snapshot.cursor_row,
                "cursor_col": snapshot.cursor_col,
                "rows": snapshot.text_rows(),
            },
            source=self.SOURCE,
        )

    def _publish_menu_event(
        self,
        event_type: EventType,
        menu: InteractiveMenu,
    ) -> None:
        """Publish a menu lifecycle event describing the detected items."""
        publish_event(
            self._bus,
            event_type,
            {
                "cell_id": self._cell_id,
                "detection": menu.detection,
                "selected_index": menu.selected_index,
                "selected_text": menu.selected_text,
                "items": [asdict(item) for item in menu.items],
            },
            source=self.SOURCE,
        )

    def _publish_menu_cleared(self) -> None:
        """Publish INTERACTIVE_MENU_CLEARED without menu contents."""
        publish_event(
            self._bus,
            EventType.INTERACTIVE_MENU_CLEARED,
            {"cell_id": self._cell_id},
            source=self.SOURCE,
        )


def _classify_osc(body: str) -> str:
    """Tag an OSC body with a rough category so bindings can filter on it.

    The OSC command number is the prefix up to the first semicolon. We
    only special-case the handful a terminal emulator actually reacts
    to; everything else is `"other"` so unknown sequences still emit an
    event but do not get mistaken for a title or a hyperlink.
    """
    prefix = body.split(";", 1)[0] if ";" in body else body
    if prefix in {"0", "1", "2"}:
        return "title"
    if prefix == "8":
        return "hyperlink"
    if prefix in {"4", "10", "11"}:
        return "color"
    return "other"


def _menus_equivalent(a: InteractiveMenu, b: InteractiveMenu) -> bool:
    """Return True when two menus show the same items and selection."""
    if a.selected_index != b.selected_index:
        return False
    if len(a.items) != len(b.items):
        return False
    for left, right in zip(a.items, b.items):
        if left.text != right.text or left.selected != right.selected:
            return False
    return True
