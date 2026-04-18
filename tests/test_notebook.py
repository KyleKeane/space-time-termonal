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

    def test_cell_navigation_tags_transition_and_carries_command(self) -> None:
        self.cursor.move_down()
        payload = self.recorder.events[-1].payload
        self.assertEqual(payload["transition"], "cell")
        self.assertEqual(payload["command"], self.cells[1].command)


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

    def test_insert_character_does_not_publish_focus_changed(self) -> None:
        self.cursor.enter_input_mode()
        self.recorder.events.clear()
        for ch in "bc":
            self.cursor.insert_character(ch)
        self.assertEqual(self.recorder.events, [])

    def test_backspace_does_not_publish_focus_changed(self) -> None:
        self.cursor.enter_input_mode()
        self.recorder.events.clear()
        self.cursor.backspace()
        self.assertEqual(self.recorder.events, [])

    def test_mode_change_tags_transition_as_mode(self) -> None:
        self.cursor.enter_input_mode()
        payload = self.recorder.events[-1].payload
        self.assertEqual(payload["transition"], "mode")
        self.assertEqual(payload["new_mode"], FocusMode.INPUT.value)
        self.assertEqual(payload["command"], self.cells[0].command)

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
        # F11: submitting a non-empty command from the last cell
        # auto-advances to a fresh empty INPUT cell so the user can
        # keep typing without pressing Ctrl+N.
        self.assertEqual(self.cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(len(self.session), 2)
        self.assertNotEqual(self.cursor.focus.cell_id, cell.cell_id)
        self.assertEqual(self.cursor.focus.input_buffer, "")

    def test_submit_outside_input_mode_returns_none(self) -> None:
        self.assertIsNone(self.cursor.submit())

    def test_new_cell_appends_and_enters_input(self) -> None:
        fresh = self.cursor.new_cell()
        self.assertEqual(self.cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(self.cursor.focus.cell_id, fresh.cell_id)
        self.assertEqual(len(self.session), 2)

    def test_submit_empty_buffer_does_not_autoadvance(self) -> None:
        """Pressing Enter on an empty buffer should not spam the
        session with empty cells. Stays in NOTEBOOK on the same cell
        — effectively a silent no-op you can back out of."""
        # Start from an empty cell so the commit produces no content.
        bus = EventBus()
        session, cells = _session_with([""])
        cursor = NotebookCursor(session, bus)
        cursor.enter_input_mode()
        cell = cursor.submit()
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(len(session), 1)
        assert cell is not None
        self.assertEqual(cell.cell_id, cells[0].cell_id)

    def test_submit_from_middle_cell_does_not_autoadvance(self) -> None:
        """Re-running an already-executed middle cell (user edited it
        and wants to re-run in place) should NOT wedge a new empty
        cell into the middle of the notebook."""
        bus = EventBus()
        session, cells = _session_with(["first", "second", "third"])
        cursor = NotebookCursor(session, bus)
        # Focus the middle cell and enter input mode.
        cursor.focus_cell(cells[1].cell_id)
        cursor.enter_input_mode()
        cursor.insert_character("x")
        cell = cursor.submit()
        assert cell is not None
        self.assertEqual(cell.command, "secondx")
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(cursor.focus.cell_id, cells[1].cell_id)
        # Session is still three cells, not four.
        self.assertEqual(len(session), 3)

    def test_submit_from_last_cell_with_content_autoadvances(self) -> None:
        """The documented happy path: type, Enter, keep typing."""
        bus = EventBus()
        session, cells = _session_with(["first"])
        cursor = NotebookCursor(session, bus)
        cursor.enter_input_mode()
        cursor.insert_character("!")
        cell = cursor.submit()
        assert cell is not None
        self.assertEqual(cell.command, "first!")
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(len(session), 2)
        # The new cell is the last cell.
        self.assertEqual(session.cells[-1].cell_id, cursor.focus.cell_id)
        # And it's empty, ready for the next command.
        self.assertEqual(cursor.focus.input_buffer, "")


class CursorOutputModeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = _Recorder(self.bus)
        self.session, self.cells = _session_with(["echo out"])
        self.cursor = NotebookCursor(self.session, self.bus)

    def test_view_output_mode_transitions(self) -> None:
        result = self.cursor.view_output_mode()
        assert result is not None
        self.assertEqual(self.cursor.focus.mode, FocusMode.OUTPUT)
        self.assertEqual(self.cursor.focus.cell_id, self.cells[0].cell_id)
        self.assertEqual(len(self.recorder.events), 1)

    def test_view_output_from_input_mode_is_noop(self) -> None:
        self.cursor.enter_input_mode()
        self.recorder.events.clear()
        self.assertIsNone(self.cursor.view_output_mode())
        self.assertEqual(self.cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(self.recorder.events, [])

    def test_exit_output_returns_to_notebook(self) -> None:
        self.cursor.view_output_mode()
        self.recorder.events.clear()
        self.cursor.exit_output_mode()
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(len(self.recorder.events), 1)

    def test_exit_output_from_notebook_is_noop(self) -> None:
        self.assertIsNone(self.cursor.exit_output_mode())
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)


class ResetInputBufferTests(unittest.TestCase):
    """`reset_input_buffer` is the ambient-meta-command helper: it
    clears the in-progress buffer while leaving the user in INPUT
    mode, and is silent on the event bus (buffer-only mutation)."""

    def test_reset_clears_buffer_without_leaving_input_mode(self) -> None:
        bus = EventBus()
        session, cells = _session_with([""])
        cursor = NotebookCursor(session, bus)
        cursor.enter_input_mode()
        for ch in ":help":
            cursor.insert_character(ch)
        self.assertEqual(cursor.focus.input_buffer, ":help")
        recorder = _Recorder(bus)
        cursor.reset_input_buffer()
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cursor.focus.cell_id, cells[0].cell_id)
        self.assertEqual(cursor.focus.input_buffer, "")
        # Buffer-only mutations must NOT publish FOCUS_CHANGED (matches
        # the contract for insert_character and backspace).
        self.assertEqual(recorder.events, [])

    def test_reset_outside_input_mode_is_noop(self) -> None:
        bus = EventBus()
        session, _ = _session_with(["echo"])
        cursor = NotebookCursor(session, bus)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        cursor.reset_input_buffer()
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_reset_with_empty_buffer_is_noop(self) -> None:
        bus = EventBus()
        session, _ = _session_with([""])
        cursor = NotebookCursor(session, bus)
        cursor.enter_input_mode()
        recorder = _Recorder(bus)
        cursor.reset_input_buffer()
        self.assertEqual(cursor.focus.input_buffer, "")
        self.assertEqual(recorder.events, [])


if __name__ == "__main__":
    unittest.main()
