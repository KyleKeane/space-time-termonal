"""Unit tests for the InputRouter."""

from __future__ import annotations

import unittest

from asat.cell import Cell
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.input_router import InputRouter, default_bindings
from asat.keys import (
    BACKSPACE,
    DOWN,
    END,
    ENTER,
    ESCAPE,
    HOME,
    Key,
    Modifier,
    PAGE_DOWN,
    PAGE_UP,
    UP,
)
from asat.notebook import FocusMode, NotebookCursor
from asat.output_buffer import OutputBuffer, STDOUT
from asat.output_cursor import OutputCursor
from asat.session import Session


def _build(commands: list[str]) -> tuple[EventBus, Session, NotebookCursor, InputRouter, list[Cell]]:
    """Construct a fresh bus/session/cursor/router stack for a test."""
    bus = EventBus()
    session = Session.new()
    cells = [Cell.new(command) for command in commands]
    for cell in cells:
        session.add_cell(cell)
    cursor = NotebookCursor(session, bus)
    router = InputRouter(cursor, bus)
    return bus, session, cursor, router, cells


class _Recorder:
    """Captures every event on a bus so tests can assert on sequences."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def types_of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


class DefaultBindingsTests(unittest.TestCase):

    def test_default_bindings_cover_both_modes(self) -> None:
        bindings = default_bindings()
        self.assertIn(FocusMode.NOTEBOOK, bindings)
        self.assertIn(FocusMode.INPUT, bindings)
        self.assertIn(UP, bindings[FocusMode.NOTEBOOK])
        self.assertIn(BACKSPACE, bindings[FocusMode.INPUT])


class NotebookModeDispatchTests(unittest.TestCase):

    def test_up_moves_cursor_up(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b", "c"])
        cursor.move_to_bottom()
        result = router.handle_key(UP)
        self.assertEqual(result, "move_up")
        self.assertEqual(cursor.focus.cell_id, cells[1].cell_id)

    def test_down_moves_cursor_down(self) -> None:
        _, _, cursor, router, cells = _build(["a", "b"])
        self.assertEqual(router.handle_key(DOWN), "move_down")
        self.assertEqual(cursor.focus.cell_id, cells[1].cell_id)

    def test_home_jumps_to_top(self) -> None:
        _, _, cursor, router, cells = _build(["a", "b", "c"])
        cursor.move_to_bottom()
        self.assertEqual(router.handle_key(HOME), "move_to_top")
        self.assertEqual(cursor.focus.cell_id, cells[0].cell_id)

    def test_enter_transitions_to_input_mode(self) -> None:
        _, _, cursor, router, _cells = _build(["echo"])
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)

    def test_ctrl_n_appends_new_cell(self) -> None:
        _, session, cursor, router, _ = _build(["a"])
        self.assertEqual(router.handle_key(Key.combo("n", Modifier.CTRL)), "new_cell")
        self.assertEqual(len(session), 2)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)

    def test_unbound_key_returns_none(self) -> None:
        _, _, _, router, _ = _build(["a"])
        self.assertIsNone(router.handle_key(Key.printable("x")))

    def test_every_key_publishes_key_pressed(self) -> None:
        bus, _, _, router, _ = _build(["a"])
        recorder = _Recorder(bus)
        router.handle_key(DOWN)
        router.handle_key(Key.printable("q"))
        self.assertEqual(len(recorder.types_of(EventType.KEY_PRESSED)), 2)


class InputModeDispatchTests(unittest.TestCase):

    def test_printable_characters_extend_buffer(self) -> None:
        _, _, cursor, router, cells = _build(["echo"])
        router.handle_key(ENTER)
        for char in " hi":
            router.handle_key(Key.printable(char))
        self.assertEqual(cursor.focus.input_buffer, "echo hi")

    def test_backspace_in_input_mode_removes_character(self) -> None:
        _, _, cursor, router, _ = _build(["echo"])
        router.handle_key(ENTER)
        router.handle_key(BACKSPACE)
        self.assertEqual(cursor.focus.input_buffer, "ech")

    def test_escape_commits_and_exits(self) -> None:
        _, _, cursor, router, cells = _build(["echo"])
        router.handle_key(ENTER)
        router.handle_key(Key.printable("x"))
        router.handle_key(ESCAPE)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(cells[0].command, "echox")

    def test_enter_submits_and_returns_to_notebook(self) -> None:
        bus, _, cursor, router, cells = _build(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        router.handle_key(Key.printable(" "))
        router.handle_key(Key.printable("z"))
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(cells[0].command, "echo z")
        submit_events = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(len(submit_events), 1)
        self.assertEqual(submit_events[0].payload["cell_id"], cells[0].cell_id)
        self.assertEqual(submit_events[0].payload["command"], "echo z")

    def test_ctrl_n_is_ignored_in_input_mode(self) -> None:
        _, session, cursor, router, _ = _build(["echo"])
        router.handle_key(ENTER)
        before = len(session)
        self.assertIsNone(router.handle_key(Key.combo("n", Modifier.CTRL)))
        self.assertEqual(len(session), before)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)


class ActionEventPayloadTests(unittest.TestCase):

    def test_insert_character_event_records_char(self) -> None:
        bus, _, _, router, _ = _build(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        router.handle_key(Key.printable("q"))
        inserts = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "insert_character"
        ]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0].payload["char"], "q")

    def test_move_action_payload_reports_focus_mode(self) -> None:
        bus, _, _, router, _ = _build(["a", "b"])
        recorder = _Recorder(bus)
        router.handle_key(DOWN)
        actions = recorder.types_of(EventType.ACTION_INVOKED)
        self.assertEqual(actions[0].payload["action"], "move_down")
        self.assertEqual(actions[0].payload["focus_mode"], FocusMode.NOTEBOOK.value)


class OutputModeDispatchTests(unittest.TestCase):

    def _stack(
        self,
        lines: list[str],
    ) -> tuple[NotebookCursor, InputRouter, OutputCursor, OutputBuffer]:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        output_cursor = OutputCursor(bus, page_size=2)
        buffer = OutputBuffer(cell_id=cell.cell_id)
        for text in lines:
            buffer.append(text, stream=STDOUT)
        output_cursor.attach(buffer)
        cursor.view_output_mode()
        router = InputRouter(cursor, bus, output_cursor=output_cursor)
        return cursor, router, output_cursor, buffer

    def test_up_moves_output_cursor_up(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c"])
        result = router.handle_key(UP)
        self.assertEqual(result, "output_line_up")
        self.assertEqual(output_cursor.line_number, 1)

    def test_down_moves_output_cursor_down(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c"])
        output_cursor.move_to_start()
        self.assertEqual(router.handle_key(DOWN), "output_line_down")
        self.assertEqual(output_cursor.line_number, 1)

    def test_page_up_and_page_down_use_page_size(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c", "d", "e"])
        router.handle_key(PAGE_UP)
        self.assertEqual(output_cursor.line_number, 2)
        router.handle_key(PAGE_DOWN)
        self.assertEqual(output_cursor.line_number, 4)

    def test_home_and_end_jump_to_ends(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c"])
        router.handle_key(HOME)
        self.assertEqual(output_cursor.line_number, 0)
        router.handle_key(END)
        self.assertEqual(output_cursor.line_number, 2)

    def test_escape_exits_output_mode(self) -> None:
        cursor, router, _, _ = self._stack(["a"])
        self.assertEqual(router.handle_key(ESCAPE), "exit_output")
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_output_actions_noop_without_output_cursor(self) -> None:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        cursor.view_output_mode()
        router = InputRouter(cursor, bus)
        self.assertEqual(router.handle_key(UP), "output_line_up")

    def test_ctrl_o_enters_output_mode_from_notebook(self) -> None:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        router = InputRouter(cursor, bus)
        result = router.handle_key(Key.combo("o", Modifier.CTRL))
        self.assertEqual(result, "view_output")
        self.assertEqual(cursor.focus.mode, FocusMode.OUTPUT)


class CustomBindingTests(unittest.TestCase):

    def test_custom_bindings_override_defaults(self) -> None:
        custom = {
            FocusMode.NOTEBOOK: {Key.printable("j"): "move_down"},
            FocusMode.INPUT: {},
        }
        bus = EventBus()
        session = Session.new()
        for command in ("a", "b"):
            session.add_cell(Cell.new(command))
        cursor = NotebookCursor(session, bus)
        router = InputRouter(cursor, bus, bindings=custom)
        self.assertEqual(router.handle_key(Key.printable("j")), "move_down")
        self.assertIsNone(router.handle_key(UP))


if __name__ == "__main__":
    unittest.main()
