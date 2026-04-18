"""Unit tests for the NotebookCursor."""

from __future__ import annotations

import unittest

from asat.cell import Cell, CellStatus
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.notebook import FocusMode, NotebookCursor
from asat.session import Session


def _session_with(commands: list[str]) -> tuple[Session, list[Cell]]:
    """Build a session pre-populated with the given commands."""
    session = Session.new()
    cells = [Cell.new(command) for command in commands]
    for cell in cells:
        session.add_cell(cell)
    return session, cells


class _Recorder:
    """Collects every FOCUS_CHANGED event fired on a bus."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(EventType.FOCUS_CHANGED, self.events.append)


class CursorInitialStateTests(unittest.TestCase):

    def test_empty_session_has_no_focus(self) -> None:
        bus = EventBus()
        session = Session.new()
        cursor = NotebookCursor(session, bus)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertIsNone(cursor.focus.cell_id)

    def test_first_cell_focused_by_default(self) -> None:
        bus = EventBus()
        session, cells = _session_with(["a", "b"])
        cursor = NotebookCursor(session, bus)
        self.assertEqual(cursor.focus.cell_id, cells[0].cell_id)
        self.assertEqual(session.active_cell_id, cells[0].cell_id)

    def test_preexisting_active_cell_is_respected(self) -> None:
        bus = EventBus()
        session, cells = _session_with(["a", "b", "c"])
        session.set_active(cells[2].cell_id)
        cursor = NotebookCursor(session, bus)
        self.assertEqual(cursor.focus.cell_id, cells[2].cell_id)


class CursorNavigationTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = _Recorder(self.bus)
        self.session, self.cells = _session_with(["a", "b", "c"])
        self.cursor = NotebookCursor(self.session, self.bus)

    def test_move_down_advances(self) -> None:
        moved = self.cursor.move_down()
        assert moved is not None
        self.assertEqual(moved.cell_id, self.cells[1].cell_id)
        self.assertEqual(self.cursor.focus.cell_id, self.cells[1].cell_id)

    def test_move_up_at_top_is_noop(self) -> None:
        result = self.cursor.move_up()
        self.assertIsNone(result)
        self.assertEqual(self.cursor.focus.cell_id, self.cells[0].cell_id)
        self.assertEqual(self.recorder.events, [])

    def test_move_down_past_end_is_noop(self) -> None:
        self.cursor.move_to_bottom()
        self.recorder.events.clear()
        result = self.cursor.move_down()
        self.assertIsNone(result)
        self.assertEqual(self.recorder.events, [])

    def test_move_to_ends(self) -> None:
        top = self.cursor.move_to_top()
        bottom = self.cursor.move_to_bottom()
        assert top is not None and bottom is not None
        self.assertEqual(top.cell_id, self.cells[0].cell_id)
        self.assertEqual(bottom.cell_id, self.cells[-1].cell_id)

    def test_navigation_publishes_focus_changed(self) -> None:
        self.cursor.move_down()
        self.assertEqual(len(self.recorder.events), 1)
        payload = self.recorder.events[0].payload
        self.assertEqual(payload["new_cell_id"], self.cells[1].cell_id)
        self.assertEqual(payload["new_mode"], FocusMode.NOTEBOOK.value)


class CursorInputModeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = _Recorder(self.bus)
        self.session, self.cells = _session_with(["echo a"])
        self.cursor = NotebookCursor(self.session, self.bus)

    def test_enter_input_mode_seeds_buffer_from_cell(self) -> None:
        self.cursor.enter_input_mode()
        self.assertEqual(self.cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(self.cursor.focus.input_buffer, "echo a")

    def test_insert_character_appends_to_buffer(self) -> None:
        self.cursor.enter_input_mode()
        for ch in "bc":
            self.cursor.insert_character(ch)
        self.assertEqual(self.cursor.focus.input_buffer, "echo abc")

    def test_insert_character_ignored_in_notebook_mode(self) -> None:
        self.cursor.insert_character("x")
        self.assertEqual(self.cursor.focus.input_buffer, "")

    def test_backspace_removes_last_character(self) -> None:
        self.cursor.enter_input_mode()
        self.cursor.backspace()
        self.assertEqual(self.cursor.focus.input_buffer, "echo ")

    def test_backspace_at_empty_buffer_is_noop(self) -> None:
        self.cursor.enter_input_mode()
        self.cursor.backspace()
        self.cursor.backspace()
        self.cursor.backspace()
        self.cursor.backspace()
        self.cursor.backspace()
        self.cursor.backspace()
        self.cursor.backspace()
        self.assertEqual(self.cursor.focus.input_buffer, "")

    def test_exit_input_mode_commits_buffer(self) -> None:
        self.cursor.enter_input_mode()
        self.cursor.insert_character("x")
        self.cursor.exit_input_mode()
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(self.cells[0].command, "echo ax")
        self.assertEqual(self.cells[0].status, CellStatus.PENDING)

    def test_submit_commits_and_returns_cell(self) -> None:
        self.cursor.enter_input_mode()
        self.cursor.insert_character("z")
        cell = self.cursor.submit()
        assert cell is not None
        self.assertEqual(cell.command, "echo az")
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_submit_outside_input_mode_returns_none(self) -> None:
        self.assertIsNone(self.cursor.submit())

    def test_new_cell_appends_and_enters_input(self) -> None:
        fresh = self.cursor.new_cell()
        self.assertEqual(self.cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(self.cursor.focus.cell_id, fresh.cell_id)
        self.assertEqual(len(self.session), 2)


if __name__ == "__main__":
    unittest.main()
