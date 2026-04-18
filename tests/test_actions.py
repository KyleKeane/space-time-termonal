"""Unit tests for the contextual action catalog, menu, and defaults."""

from __future__ import annotations

import unittest

from asat.actions import (
    ActionCatalog,
    ActionContext,
    ActionMenu,
    MemoryClipboard,
    MenuItem,
    default_actions,
)
from asat.cell import Cell
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.notebook import FocusMode, NotebookCursor
from asat.output_buffer import OutputRecorder, STDERR, STDOUT
from asat.output_cursor import OutputCursor
from asat.session import Session


class _Recorder:
    """Captures every event on a bus so tests can assert on sequences."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


def _noop_handler(_context: ActionContext) -> None:
    """Sentinel handler used to verify plumbing without side effects."""


class ActionCatalogTests(unittest.TestCase):

    def test_providers_contribute_items_in_registration_order(self) -> None:
        catalog = ActionCatalog()
        catalog.register(
            FocusMode.NOTEBOOK,
            lambda ctx: (MenuItem(id="a", label="A", handler=_noop_handler),),
        )
        catalog.register(
            FocusMode.NOTEBOOK,
            lambda ctx: (MenuItem(id="b", label="B", handler=_noop_handler),),
        )
        context = ActionContext(focus_mode=FocusMode.NOTEBOOK)
        items = catalog.items_for(context)
        self.assertEqual([item.id for item in items], ["a", "b"])

    def test_unknown_mode_returns_empty(self) -> None:
        catalog = ActionCatalog()
        context = ActionContext(focus_mode=FocusMode.OUTPUT)
        self.assertEqual(catalog.items_for(context), ())


class ActionMenuTests(unittest.TestCase):

    def _menu_with(self, items: list[MenuItem]) -> tuple[ActionMenu, _Recorder, EventBus]:
        bus = EventBus()
        catalog = ActionCatalog()
        catalog.register(FocusMode.NOTEBOOK, lambda ctx: tuple(items))
        menu = ActionMenu(bus, catalog)
        return menu, _Recorder(bus), bus

    def test_open_focuses_first_item_and_publishes(self) -> None:
        items = [
            MenuItem(id="one", label="One", handler=_noop_handler),
            MenuItem(id="two", label="Two", handler=_noop_handler),
        ]
        menu, recorder, _ = self._menu_with(items)
        opened = menu.open(ActionContext(focus_mode=FocusMode.NOTEBOOK))
        self.assertEqual([item.id for item in opened], ["one", "two"])
        self.assertTrue(menu.is_open)
        self.assertEqual(menu.current_item().id, "one")
        open_events = recorder.of(EventType.ACTION_MENU_OPENED)
        focus_events = recorder.of(EventType.ACTION_MENU_ITEM_FOCUSED)
        self.assertEqual(len(open_events), 1)
        self.assertEqual(open_events[0].payload["item_ids"], ["one", "two"])
        self.assertEqual(len(focus_events), 1)
        self.assertEqual(focus_events[0].payload["item_id"], "one")

    def test_focus_next_walks_forward_and_clamps(self) -> None:
        items = [
            MenuItem(id=f"i{n}", label=str(n), handler=_noop_handler)
            for n in range(3)
        ]
        menu, recorder, _ = self._menu_with(items)
        menu.open(ActionContext(focus_mode=FocusMode.NOTEBOOK))
        menu.focus_next()
        self.assertEqual(menu.current_item().id, "i1")
        menu.focus_next()
        self.assertEqual(menu.current_item().id, "i2")
        before = len(recorder.of(EventType.ACTION_MENU_ITEM_FOCUSED))
        menu.focus_next()
        self.assertEqual(menu.current_item().id, "i2")
        self.assertEqual(
            len(recorder.of(EventType.ACTION_MENU_ITEM_FOCUSED)),
            before,
        )

    def test_activate_calls_handler_and_closes_menu(self) -> None:
        received: list[ActionContext] = []

        def capture(context: ActionContext) -> None:
            received.append(context)

        items = [MenuItem(id="a", label="A", handler=capture)]
        menu, recorder, _ = self._menu_with(items)
        context = ActionContext(
            focus_mode=FocusMode.NOTEBOOK, cell_id="cell-42"
        )
        menu.open(context)
        activated = menu.activate()
        assert activated is not None
        self.assertEqual(activated.id, "a")
        self.assertFalse(menu.is_open)
        self.assertEqual(received, [context])
        self.assertEqual(len(recorder.of(EventType.ACTION_MENU_ITEM_INVOKED)), 1)
        self.assertEqual(len(recorder.of(EventType.ACTION_MENU_CLOSED)), 1)

    def test_close_without_open_is_noop(self) -> None:
        menu, recorder, _ = self._menu_with([])
        menu.close()
        self.assertEqual(recorder.of(EventType.ACTION_MENU_CLOSED), [])

    def test_open_with_no_providers_emits_opened_but_stays_closed(self) -> None:
        bus = EventBus()
        catalog = ActionCatalog()
        menu = ActionMenu(bus, catalog)
        recorder = _Recorder(bus)
        result = menu.open(ActionContext(focus_mode=FocusMode.NOTEBOOK))
        self.assertEqual(result, ())
        self.assertFalse(menu.is_open)
        self.assertEqual(len(recorder.of(EventType.ACTION_MENU_OPENED)), 1)
        self.assertEqual(recorder.of(EventType.ACTION_MENU_ITEM_FOCUSED), [])


class DefaultActionsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.session = Session.new()
        self.cell = Cell.new("echo hi")
        self.session.add_cell(self.cell)
        self.cursor = NotebookCursor(self.session, self.bus)
        self.recorder = OutputRecorder(self.bus)
        self.output_cursor = OutputCursor(self.bus)
        self.clipboard = MemoryClipboard()
        self.catalog = default_actions(
            self.cursor,
            self.recorder,
            self.output_cursor,
            self.clipboard,
            self.bus,
        )

    def test_notebook_items_include_edit_and_explore(self) -> None:
        context = ActionContext(
            focus_mode=FocusMode.NOTEBOOK,
            cell_id=self.cell.cell_id,
        )
        items = self.catalog.items_for(context)
        ids = [item.id for item in items]
        self.assertIn("enter_input", ids)
        self.assertIn("view_output", ids)

    def test_notebook_items_empty_when_no_cell(self) -> None:
        context = ActionContext(focus_mode=FocusMode.NOTEBOOK)
        self.assertEqual(self.catalog.items_for(context), ())

    def test_view_output_handler_transitions_cursor(self) -> None:
        self.recorder.buffer_for(self.cell.cell_id).append("line-a", STDOUT)
        context = ActionContext(
            focus_mode=FocusMode.NOTEBOOK,
            cell_id=self.cell.cell_id,
        )
        items = self.catalog.items_for(context)
        handler = next(item for item in items if item.id == "view_output").handler
        handler(context)
        self.assertEqual(self.cursor.focus.mode, FocusMode.OUTPUT)
        self.assertEqual(self.output_cursor.line_number, 0)

    def test_copy_line_uses_clipboard_and_publishes_event(self) -> None:
        context = ActionContext(
            focus_mode=FocusMode.OUTPUT,
            cell_id=self.cell.cell_id,
            line_number=2,
            line_stream=STDOUT,
            line_text="the line",
        )
        items = self.catalog.items_for(context)
        handler = next(item for item in items if item.id == "copy_line").handler
        handler(context)
        self.assertEqual(self.clipboard.text, "the line")

    def test_copy_all_joins_every_stream(self) -> None:
        buffer = self.recorder.buffer_for(self.cell.cell_id)
        buffer.append("alpha", STDOUT)
        buffer.append("beta", STDERR)
        buffer.append("gamma", STDOUT)
        context = ActionContext(
            focus_mode=FocusMode.OUTPUT,
            cell_id=self.cell.cell_id,
        )
        items = self.catalog.items_for(context)
        handler = next(item for item in items if item.id == "copy_all").handler
        handler(context)
        self.assertEqual(self.clipboard.text, "alpha\nbeta\ngamma")

    def test_copy_stderr_only_includes_stderr_lines(self) -> None:
        buffer = self.recorder.buffer_for(self.cell.cell_id)
        buffer.append("ignore", STDOUT)
        buffer.append("oops", STDERR)
        buffer.append("still oops", STDERR)
        context = ActionContext(
            focus_mode=FocusMode.OUTPUT,
            cell_id=self.cell.cell_id,
        )
        items = self.catalog.items_for(context)
        handler = next(item for item in items if item.id == "copy_stderr").handler
        handler(context)
        self.assertEqual(self.clipboard.text, "oops\nstill oops")

    def test_output_items_drop_copy_line_without_focused_line(self) -> None:
        context = ActionContext(
            focus_mode=FocusMode.OUTPUT,
            cell_id=self.cell.cell_id,
        )
        items = self.catalog.items_for(context)
        self.assertNotIn("copy_line", [item.id for item in items])
        self.assertIn("exit_output", [item.id for item in items])

    def test_exit_output_handler_returns_to_notebook(self) -> None:
        self.cursor.view_output_mode()
        context = ActionContext(
            focus_mode=FocusMode.OUTPUT,
            cell_id=self.cell.cell_id,
        )
        items = self.catalog.items_for(context)
        handler = next(item for item in items if item.id == "exit_output").handler
        handler(context)
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_clipboard_copied_event_describes_source(self) -> None:
        events = _Recorder(self.bus)
        context = ActionContext(
            focus_mode=FocusMode.OUTPUT,
            cell_id=self.cell.cell_id,
            line_text="hey",
        )
        items = self.catalog.items_for(context)
        handler = next(item for item in items if item.id == "copy_line").handler
        handler(context)
        copied = events.of(EventType.CLIPBOARD_COPIED)
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].payload["source"], "line")
        self.assertEqual(copied[0].payload["length"], 3)


if __name__ == "__main__":
    unittest.main()
