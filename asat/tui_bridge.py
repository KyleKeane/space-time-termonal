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

from asat.ansi import AnsiParser
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.interactive import InteractiveMenu, detect
from asat.screen import DEFAULT_COLS, DEFAULT_ROWS, VirtualScreen


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
        INTERACTIVE_MENU_UPDATED / INTERACTIVE_MENU_CLEARED.
        """
        if not data:
            return self._menu
        tokens = self._parser.feed(data)
        if not tokens:
            return self._menu
        self._screen.apply_all(tokens)
        self._publish_screen_update()
        new_menu = detect(self._screen.snapshot())
        self._transition_menu(new_menu)
        return self._menu

    def finish(self) -> Optional[InteractiveMenu]:
        """Flush any buffered text and return the final menu state."""
        tokens = self._parser.finish()
        if tokens:
            self._screen.apply_all(tokens)
            self._publish_screen_update()
            new_menu = detect(self._screen.snapshot())
            self._transition_menu(new_menu)
        return self._menu

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
        self._bus.publish(
            Event(
                event_type=EventType.SCREEN_UPDATED,
                payload={
                    "cell_id": self._cell_id,
                    "cursor_row": snapshot.cursor_row,
                    "cursor_col": snapshot.cursor_col,
                    "rows": snapshot.text_rows(),
                },
                source=self.SOURCE,
            )
        )

    def _publish_menu_event(
        self,
        event_type: EventType,
        menu: InteractiveMenu,
    ) -> None:
        """Publish a menu lifecycle event describing the detected items."""
        self._bus.publish(
            Event(
                event_type=event_type,
                payload={
                    "cell_id": self._cell_id,
                    "detection": menu.detection,
                    "selected_index": menu.selected_index,
                    "selected_text": menu.selected_text,
                    "items": [asdict(item) for item in menu.items],
                },
                source=self.SOURCE,
            )
        )

    def _publish_menu_cleared(self) -> None:
        """Publish INTERACTIVE_MENU_CLEARED without menu contents."""
        self._bus.publish(
            Event(
                event_type=EventType.INTERACTIVE_MENU_CLEARED,
                payload={"cell_id": self._cell_id},
                source=self.SOURCE,
            )
        )


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
