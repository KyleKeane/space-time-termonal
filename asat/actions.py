"""Contextual action catalog and menu.

The action system is how a blind user reaches "right-click" style
affordances without a mouse. It is organized into three layers:

ActionContext
    A frozen snapshot describing what the user is focused on:
    focus mode, current cell, and, when in OUTPUT mode, the focused
    line's number, stream, and text. Providers consult the context
    to decide which items they contribute.

ActionCatalog
    A registry of ActionProvider callables keyed by FocusMode. Given
    an ActionContext, the catalog asks each provider registered for
    the context's focus mode for a sequence of MenuItems and returns
    the aggregated list in registration order.

ActionMenu
    The stateful picker the user drives with keystrokes. It opens
    against a context, tracks which item is focused, publishes menu
    events, and activates the current item when asked. Activation
    invokes the item's handler with the original context.

Clipboard is declared as a small Protocol so later phases can plug in
an OS-native backend. A simple in-memory MemoryClipboard is included
so tests and early integrations do not need any side effect outside
the process.

default_actions() wires up the built-in providers Phase 5 ships with:
entering input, viewing output, exiting modes, and copying output
lines into the clipboard. Callers that need different affordances can
build their own catalog from the same primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.notebook import FocusMode, NotebookCursor
from asat.output_buffer import OutputRecorder, STDERR, STDOUT
from asat.output_cursor import OutputCursor


@dataclass(frozen=True)
class ActionContext:
    """Snapshot of where the user is focused when the menu opens."""

    focus_mode: FocusMode
    cell_id: Optional[str] = None
    line_number: Optional[int] = None
    line_stream: Optional[str] = None
    line_text: Optional[str] = None


ActionHandler = Callable[[ActionContext], None]


@dataclass(frozen=True)
class MenuItem:
    """One selectable entry in the contextual menu."""

    id: str
    label: str
    handler: ActionHandler


ActionProvider = Callable[[ActionContext], tuple[MenuItem, ...]]


class Clipboard(Protocol):
    """Minimal contract for a clipboard backend."""

    def set_text(self, text: str) -> None:
        """Replace the clipboard contents with the given text."""


class MemoryClipboard:
    """In-process clipboard used for tests and default integrations."""

    def __init__(self) -> None:
        """Initialize with empty contents."""
        self._text = ""

    @property
    def text(self) -> str:
        """Return the most recently stored text."""
        return self._text

    def set_text(self, text: str) -> None:
        """Replace the stored text."""
        self._text = text


class ActionCatalog:
    """Registry of per-focus-mode action providers."""

    def __init__(self) -> None:
        """Create an empty catalog with no providers registered."""
        self._providers: dict[FocusMode, list[ActionProvider]] = {
            mode: [] for mode in FocusMode
        }

    def register(self, mode: FocusMode, provider: ActionProvider) -> None:
        """Append a provider for the given focus mode."""
        self._providers[mode].append(provider)

    def items_for(self, context: ActionContext) -> tuple[MenuItem, ...]:
        """Return the concatenated items for the given context."""
        result: list[MenuItem] = []
        for provider in self._providers.get(context.focus_mode, []):
            result.extend(provider(context))
        return tuple(result)


class ActionMenu:
    """Stateful picker over the items returned by an ActionCatalog."""

    SOURCE = "action_menu"

    def __init__(self, bus: EventBus, catalog: ActionCatalog) -> None:
        """Bind the menu to a bus and the catalog it draws items from."""
        self._bus = bus
        self._catalog = catalog
        self._items: tuple[MenuItem, ...] = ()
        self._index: int = -1
        self._context: Optional[ActionContext] = None

    @property
    def is_open(self) -> bool:
        """Return True if the menu is currently showing items."""
        return self._index >= 0

    @property
    def items(self) -> tuple[MenuItem, ...]:
        """Return the items currently shown by the menu."""
        return self._items

    def open(self, context: ActionContext) -> tuple[MenuItem, ...]:
        """Open the menu against a context and focus the first item.

        Publishes ACTION_MENU_OPENED with the item labels and an
        ACTION_MENU_ITEM_FOCUSED event for the first item. Returns the
        tuple of items; empty if no provider contributed anything.
        """
        items = self._catalog.items_for(context)
        self._items = items
        self._context = context
        self._index = 0 if items else -1
        self._publish_opened(context, items)
        if items:
            self._publish_focus(items[0])
        return items

    def close(self) -> None:
        """Close the menu if it is open and publish ACTION_MENU_CLOSED."""
        if not self.is_open and not self._items:
            return
        self._items = ()
        self._index = -1
        context = self._context
        self._context = None
        publish_event(
            self._bus,
            EventType.ACTION_MENU_CLOSED,
            {
                "focus_mode": context.focus_mode.value if context else None,
                "cell_id": context.cell_id if context else None,
            },
            source=self.SOURCE,
        )

    def current_item(self) -> Optional[MenuItem]:
        """Return the currently focused item or None if the menu is closed."""
        if not self.is_open:
            return None
        return self._items[self._index]

    def focus_next(self) -> Optional[MenuItem]:
        """Advance focus to the next item, clamping at the last entry."""
        return self._move_focus(self._index + 1)

    def focus_prev(self) -> Optional[MenuItem]:
        """Move focus to the previous item, clamping at the first entry."""
        return self._move_focus(self._index - 1)

    def activate(self) -> Optional[MenuItem]:
        """Invoke the focused item's handler and close the menu.

        Publishes ACTION_MENU_ITEM_INVOKED before closing. If the menu
        is closed or empty, returns None and does nothing.
        """
        item = self.current_item()
        context = self._context
        if item is None or context is None:
            return None
        publish_event(
            self._bus,
            EventType.ACTION_MENU_ITEM_INVOKED,
            {
                "item_id": item.id,
                "label": item.label,
                "focus_mode": context.focus_mode.value,
                "cell_id": context.cell_id,
            },
            source=self.SOURCE,
        )
        item.handler(context)
        self.close()
        return item

    def _move_focus(self, target: int) -> Optional[MenuItem]:
        """Clamp target into range, move, and publish on real change."""
        if not self.is_open or not self._items:
            return None
        clamped = max(0, min(target, len(self._items) - 1))
        if clamped == self._index:
            return self._items[self._index]
        self._index = clamped
        item = self._items[self._index]
        self._publish_focus(item)
        return item

    def _publish_opened(
        self,
        context: ActionContext,
        items: tuple[MenuItem, ...],
    ) -> None:
        """Publish ACTION_MENU_OPENED with the item labels for this context."""
        publish_event(
            self._bus,
            EventType.ACTION_MENU_OPENED,
            {
                "focus_mode": context.focus_mode.value,
                "cell_id": context.cell_id,
                "item_ids": [item.id for item in items],
                "labels": [item.label for item in items],
            },
            source=self.SOURCE,
        )

    def _publish_focus(self, item: MenuItem) -> None:
        """Publish ACTION_MENU_ITEM_FOCUSED for the given item."""
        publish_event(
            self._bus,
            EventType.ACTION_MENU_ITEM_FOCUSED,
            {
                "item_id": item.id,
                "label": item.label,
                "index": self._index,
            },
            source=self.SOURCE,
        )


def default_actions(
    cursor: NotebookCursor,
    recorder: OutputRecorder,
    output_cursor: OutputCursor,
    clipboard: Clipboard,
    bus: EventBus,
) -> ActionCatalog:
    """Build the catalog of built-in Phase 5 actions.

    NOTEBOOK mode exposes entering input and viewing output. INPUT mode
    exposes submit and cancel. OUTPUT mode exposes copying the focused
    line, copying the whole buffer, copying only stderr, and exiting
    back to the notebook.

    Copy actions route through the Clipboard and publish a
    CLIPBOARD_COPIED event describing what landed on the clipboard.
    """
    catalog = ActionCatalog()

    def _copy(context: ActionContext, text: str, source_label: str) -> None:
        """Store text on the clipboard and publish CLIPBOARD_COPIED."""
        clipboard.set_text(text)
        publish_event(
            bus,
            EventType.CLIPBOARD_COPIED,
            {
                "cell_id": context.cell_id,
                "source": source_label,
                "length": len(text),
            },
            source="actions",
        )

    def notebook_items(context: ActionContext) -> tuple[MenuItem, ...]:
        """Enter input / view output items for a focused cell."""
        if context.cell_id is None:
            return ()
        return (
            MenuItem(
                id="enter_input",
                label="Edit command",
                handler=lambda _ctx: cursor.enter_input_mode(),
            ),
            MenuItem(
                id="view_output",
                label="Explore output",
                handler=lambda _ctx: _enter_output(context),
            ),
        )

    def _enter_output(context: ActionContext) -> None:
        """Transition the cursor to OUTPUT mode and attach the cursor."""
        cursor.view_output_mode()
        if context.cell_id is not None:
            output_cursor.attach(recorder.buffer_for(context.cell_id))

    def input_items(_context: ActionContext) -> tuple[MenuItem, ...]:
        """Submit / cancel items for the editing view."""
        return (
            MenuItem(
                id="submit",
                label="Submit command",
                handler=lambda _ctx: cursor.submit(),
            ),
            MenuItem(
                id="exit_input",
                label="Cancel editing",
                handler=lambda _ctx: cursor.exit_input_mode(),
            ),
        )

    def output_items(context: ActionContext) -> tuple[MenuItem, ...]:
        """Copy / exit items for the output exploration view."""
        items: list[MenuItem] = []
        if context.line_text is not None:
            items.append(
                MenuItem(
                    id="copy_line",
                    label="Copy focused line",
                    handler=lambda ctx: _copy(
                        ctx,
                        ctx.line_text or "",
                        "line",
                    ),
                )
            )
        if context.cell_id is not None:
            items.append(
                MenuItem(
                    id="copy_all",
                    label="Copy all output",
                    handler=lambda ctx: _copy(
                        ctx,
                        _buffer_text(ctx, (STDOUT, STDERR)),
                        "all",
                    ),
                )
            )
            items.append(
                MenuItem(
                    id="copy_stderr",
                    label="Copy error output",
                    handler=lambda ctx: _copy(
                        ctx,
                        _buffer_text(ctx, (STDERR,)),
                        "stderr",
                    ),
                )
            )
        items.append(
            MenuItem(
                id="exit_output",
                label="Return to notebook",
                handler=lambda _ctx: cursor.exit_output_mode(),
            )
        )
        return tuple(items)

    def _buffer_text(context: ActionContext, streams: tuple[str, ...]) -> str:
        """Join the selected streams from the cell's buffer into one string."""
        if context.cell_id is None or not recorder.has_buffer_for(context.cell_id):
            return ""
        buffer = recorder.buffer_for(context.cell_id)
        return "\n".join(
            line.text for line in buffer.lines() if line.stream in streams
        )

    catalog.register(FocusMode.NOTEBOOK, notebook_items)
    catalog.register(FocusMode.INPUT, input_items)
    catalog.register(FocusMode.OUTPUT, output_items)
    return catalog
