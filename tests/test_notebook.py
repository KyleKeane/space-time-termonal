"""Unit tests for the NotebookCursor."""

from __future__ import annotations

import unittest

from asat.cell import Cell, CellKind, CellStatus
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


class InLineBufferEditingTests(unittest.TestCase):
    """F13: caret tracking, in-place insert, and readline-style kill
    shortcuts inside the input buffer."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.recorder = _Recorder(self.bus)
        self.session, self.cells = _session_with(["echo hello"])
        self.cursor = NotebookCursor(self.session, self.bus)
        self.cursor.enter_input_mode()
        # Drop the focus-changed event caused by entering input mode.
        self.recorder.events.clear()

    def test_enter_input_mode_places_caret_at_end(self) -> None:
        self.assertEqual(self.cursor.focus.cursor_position, len("echo hello"))

    def test_cursor_left_and_right_move_one_character(self) -> None:
        self.cursor.cursor_left()
        self.cursor.cursor_left()
        self.assertEqual(self.cursor.focus.cursor_position, len("echo hello") - 2)
        self.cursor.cursor_right()
        self.assertEqual(self.cursor.focus.cursor_position, len("echo hello") - 1)

    def test_cursor_motion_does_not_publish_focus_changed(self) -> None:
        self.cursor.cursor_left()
        self.cursor.cursor_right()
        self.cursor.cursor_home()
        self.cursor.cursor_end()
        self.assertEqual(self.recorder.events, [])

    def test_cursor_left_clamps_at_start(self) -> None:
        self.cursor.cursor_home()
        self.cursor.cursor_left()
        self.assertEqual(self.cursor.focus.cursor_position, 0)

    def test_cursor_right_clamps_at_end(self) -> None:
        # Already at end from setUp.
        self.cursor.cursor_right()
        self.assertEqual(self.cursor.focus.cursor_position, len("echo hello"))

    def test_cursor_home_and_end(self) -> None:
        self.cursor.cursor_home()
        self.assertEqual(self.cursor.focus.cursor_position, 0)
        self.cursor.cursor_end()
        self.assertEqual(self.cursor.focus.cursor_position, len("echo hello"))

    def test_insert_character_inserts_at_caret(self) -> None:
        self.cursor.cursor_home()
        self.cursor.insert_character("X")
        self.assertEqual(self.cursor.focus.input_buffer, "Xecho hello")
        self.assertEqual(self.cursor.focus.cursor_position, 1)

    def test_insert_character_in_middle(self) -> None:
        # Move caret between "echo" and " hello".
        for _ in range(len(" hello")):
            self.cursor.cursor_left()
        self.cursor.insert_character("!")
        self.assertEqual(self.cursor.focus.input_buffer, "echo! hello")
        self.assertEqual(self.cursor.focus.cursor_position, len("echo!"))

    def test_backspace_deletes_before_caret(self) -> None:
        # Move caret to the start of "hello"; backspace should eat the space.
        for _ in range(len("hello")):
            self.cursor.cursor_left()
        self.cursor.backspace()
        self.assertEqual(self.cursor.focus.input_buffer, "echohello")
        # Caret now sits where the space used to be (index 4).
        self.assertEqual(self.cursor.focus.cursor_position, 4)

    def test_backspace_at_start_is_noop(self) -> None:
        self.cursor.cursor_home()
        self.cursor.backspace()
        self.assertEqual(self.cursor.focus.input_buffer, "echo hello")
        self.assertEqual(self.cursor.focus.cursor_position, 0)

    def test_delete_forward_deletes_under_caret(self) -> None:
        self.cursor.cursor_home()
        self.cursor.delete_forward()
        self.assertEqual(self.cursor.focus.input_buffer, "cho hello")
        self.assertEqual(self.cursor.focus.cursor_position, 0)

    def test_delete_forward_at_end_is_noop(self) -> None:
        # Caret is at the end after setUp.
        self.cursor.delete_forward()
        self.assertEqual(self.cursor.focus.input_buffer, "echo hello")

    def test_delete_word_left_eats_preceding_word(self) -> None:
        self.cursor.delete_word_left()
        self.assertEqual(self.cursor.focus.input_buffer, "echo ")
        self.assertEqual(self.cursor.focus.cursor_position, len("echo "))

    def test_delete_word_left_eats_trailing_whitespace(self) -> None:
        # "echo hello   " with trailing whitespace — should kill both.
        for ch in "   ":
            self.cursor.insert_character(ch)
        self.cursor.delete_word_left()
        self.assertEqual(self.cursor.focus.input_buffer, "echo ")
        self.assertEqual(self.cursor.focus.cursor_position, len("echo "))

    def test_delete_word_left_at_start_is_noop(self) -> None:
        self.cursor.cursor_home()
        self.cursor.delete_word_left()
        self.assertEqual(self.cursor.focus.input_buffer, "echo hello")

    def test_delete_to_start_clears_prefix(self) -> None:
        # Move caret to just before "hello".
        for _ in range(len("hello")):
            self.cursor.cursor_left()
        self.cursor.delete_to_start()
        self.assertEqual(self.cursor.focus.input_buffer, "hello")
        self.assertEqual(self.cursor.focus.cursor_position, 0)

    def test_delete_to_end_clears_suffix(self) -> None:
        # Caret between "echo" and " hello".
        for _ in range(len(" hello")):
            self.cursor.cursor_left()
        self.cursor.delete_to_end()
        self.assertEqual(self.cursor.focus.input_buffer, "echo")
        # Caret position unchanged.
        self.assertEqual(self.cursor.focus.cursor_position, len("echo"))

    def test_motion_and_edit_outside_input_mode_are_noops(self) -> None:
        self.cursor.exit_input_mode()
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)
        self.cursor.cursor_left()
        self.cursor.cursor_right()
        self.cursor.cursor_home()
        self.cursor.cursor_end()
        self.cursor.delete_forward()
        self.cursor.delete_word_left()
        self.cursor.delete_to_start()
        self.cursor.delete_to_end()
        # None of these should have altered buffer/caret state.
        self.assertEqual(self.cursor.focus.input_buffer, "")
        self.assertEqual(self.cursor.focus.cursor_position, 0)

    def test_exiting_input_mode_resets_caret(self) -> None:
        self.cursor.cursor_home()
        self.cursor.exit_input_mode()
        self.assertEqual(self.cursor.focus.cursor_position, 0)


class CellLifecycleOperationsTests(unittest.TestCase):
    """F15: delete / duplicate / move from NOTEBOOK mode."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.created: list[Event] = []
        self.removed: list[Event] = []
        self.moved: list[Event] = []
        self.bus.subscribe(EventType.CELL_CREATED, self.created.append)
        self.bus.subscribe(EventType.CELL_REMOVED, self.removed.append)
        self.bus.subscribe(EventType.CELL_MOVED, self.moved.append)
        self.session, self.cells = _session_with(["a", "b", "c"])
        self.cursor = NotebookCursor(self.session, self.bus)

    def test_delete_removes_focused_cell_and_focuses_neighbor(self) -> None:
        self.cursor.focus_cell(self.cells[1].cell_id)
        removed = self.cursor.delete_focused_cell()
        assert removed is not None
        self.assertEqual(removed.cell_id, self.cells[1].cell_id)
        self.assertEqual(len(self.session), 2)
        # Former index 1 slid into slot 1 -> that's old cells[2].
        self.assertEqual(self.cursor.focus.cell_id, self.cells[2].cell_id)
        self.assertEqual(len(self.removed), 1)
        self.assertEqual(self.removed[0].payload["cell_id"], self.cells[1].cell_id)
        self.assertEqual(self.removed[0].payload["index"], 1)

    def test_delete_of_last_cell_focuses_new_tail(self) -> None:
        self.cursor.move_to_bottom()
        removed = self.cursor.delete_focused_cell()
        assert removed is not None
        self.assertEqual(self.cursor.focus.cell_id, self.cells[1].cell_id)

    def test_delete_of_only_cell_clears_focus(self) -> None:
        session, cells = _session_with(["only"])
        cursor = NotebookCursor(session, EventBus())
        removed = cursor.delete_focused_cell()
        assert removed is not None
        self.assertEqual(len(session), 0)
        self.assertIsNone(cursor.focus.cell_id)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_delete_outside_notebook_mode_is_noop(self) -> None:
        self.cursor.enter_input_mode()
        result = self.cursor.delete_focused_cell()
        self.assertIsNone(result)
        self.assertEqual(len(self.session), 3)
        self.assertEqual(self.removed, [])

    def test_delete_on_empty_session_is_noop(self) -> None:
        session = Session.new()
        cursor = NotebookCursor(session, EventBus())
        self.assertIsNone(cursor.delete_focused_cell())

    def test_duplicate_inserts_after_source_and_focuses_it(self) -> None:
        self.cursor.focus_cell(self.cells[0].cell_id)
        copy = self.cursor.duplicate_focused_cell()
        assert copy is not None
        self.assertEqual(copy.command, "a")
        self.assertNotEqual(copy.cell_id, self.cells[0].cell_id)
        self.assertEqual(len(self.session), 4)
        self.assertEqual(self.session.cells[1].cell_id, copy.cell_id)
        self.assertEqual(self.cursor.focus.cell_id, copy.cell_id)
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(copy.status, CellStatus.PENDING)

    def test_duplicate_publishes_cell_created(self) -> None:
        self.created.clear()
        copy = self.cursor.duplicate_focused_cell()
        assert copy is not None
        self.assertEqual(len(self.created), 1)
        payload = self.created[0].payload
        self.assertEqual(payload["cell_id"], copy.cell_id)
        self.assertEqual(payload["command"], copy.command)

    def test_duplicate_outside_notebook_mode_is_noop(self) -> None:
        self.cursor.enter_input_mode()
        self.assertIsNone(self.cursor.duplicate_focused_cell())

    def test_move_up_shifts_focused_cell(self) -> None:
        self.cursor.focus_cell(self.cells[2].cell_id)
        moved = self.cursor.move_focused_cell(-1)
        self.assertTrue(moved)
        self.assertEqual(
            [cell.cell_id for cell in self.session.cells],
            [self.cells[0].cell_id, self.cells[2].cell_id, self.cells[1].cell_id],
        )
        self.assertEqual(self.cursor.focus.cell_id, self.cells[2].cell_id)
        self.assertEqual(len(self.moved), 1)
        payload = self.moved[0].payload
        self.assertEqual(payload["old_index"], 2)
        self.assertEqual(payload["new_index"], 1)

    def test_move_down_shifts_focused_cell(self) -> None:
        self.cursor.focus_cell(self.cells[0].cell_id)
        self.assertTrue(self.cursor.move_focused_cell(+1))
        self.assertEqual(self.session.cells[1].cell_id, self.cells[0].cell_id)

    def test_move_at_boundary_is_noop(self) -> None:
        self.cursor.focus_cell(self.cells[0].cell_id)
        self.assertFalse(self.cursor.move_focused_cell(-1))
        self.assertEqual(self.moved, [])
        self.cursor.focus_cell(self.cells[-1].cell_id)
        self.assertFalse(self.cursor.move_focused_cell(+1))
        self.assertEqual(self.moved, [])

    def test_move_outside_notebook_mode_is_noop(self) -> None:
        self.cursor.enter_input_mode()
        self.assertFalse(self.cursor.move_focused_cell(-1))

    def test_new_cell_publishes_cell_created(self) -> None:
        before = len(self.created)
        fresh = self.cursor.new_cell("hello")
        self.assertEqual(len(self.created) - before, 1)
        payload = self.created[-1].payload
        self.assertEqual(payload["cell_id"], fresh.cell_id)
        self.assertEqual(payload["command"], "hello")

    def test_submit_autoadvance_publishes_cell_created(self) -> None:
        self.cursor.move_to_bottom()
        self.cursor.enter_input_mode()
        self.cursor.insert_character("!")
        self.created.clear()
        self.cursor.submit()
        self.assertEqual(len(self.created), 1)


class HeadingNavigationTests(unittest.TestCase):
    """F61: headings act as NVDA-style jump targets."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.session = Session.new()
        # Interleave headings and commands to exercise level filtering
        # and any-level cycling.
        #   [0] h1 "Intro"
        #   [1] cmd "ls"
        #   [2] h2 "Setup"
        #   [3] cmd "install"
        #   [4] h1 "Runs"
        #   [5] cmd "run"
        self.session.add_cell(Cell.new_heading(1, "Intro"))
        self.session.add_cell(Cell.new("ls"))
        self.session.add_cell(Cell.new_heading(2, "Setup"))
        self.session.add_cell(Cell.new("install"))
        self.session.add_cell(Cell.new_heading(1, "Runs"))
        self.session.add_cell(Cell.new("run"))
        self.cursor = NotebookCursor(self.session, self.bus)
        # Start at the first cell; explicit for clarity.
        self.cursor.focus_cell(self.session.cells[0].cell_id)

    def test_next_any_level_skips_current_and_walks_order(self) -> None:
        # From index 0 (h1 Intro) -> index 2 (h2 Setup)
        landed = self.cursor.move_to_next_heading()
        assert landed is not None
        self.assertEqual(landed.heading_title, "Setup")
        # Next -> index 4 (h1 Runs)
        landed = self.cursor.move_to_next_heading()
        assert landed is not None
        self.assertEqual(landed.heading_title, "Runs")
        # Next -> None (no more headings); cursor does not move
        before = self.cursor.focus.cell_id
        self.assertIsNone(self.cursor.move_to_next_heading())
        self.assertEqual(self.cursor.focus.cell_id, before)

    def test_prev_any_level_walks_back(self) -> None:
        self.cursor.focus_cell(self.session.cells[-1].cell_id)
        landed = self.cursor.move_to_previous_heading()
        assert landed is not None
        self.assertEqual(landed.heading_title, "Runs")
        landed = self.cursor.move_to_previous_heading()
        assert landed is not None
        self.assertEqual(landed.heading_title, "Setup")
        landed = self.cursor.move_to_previous_heading()
        assert landed is not None
        self.assertEqual(landed.heading_title, "Intro")
        self.assertIsNone(self.cursor.move_to_previous_heading())

    def test_level_filter_finds_matching_level_only(self) -> None:
        # Level 1 from index 0: skip current, next match is index 4
        landed = self.cursor.move_to_next_heading(level=1)
        assert landed is not None
        self.assertEqual(landed.heading_title, "Runs")
        # No more h1 after that.
        self.assertIsNone(self.cursor.move_to_next_heading(level=1))

    def test_level_filter_no_match_leaves_cursor_alone(self) -> None:
        # There are no h6 cells; cursor should not move.
        before = self.cursor.focus.cell_id
        self.assertIsNone(self.cursor.move_to_next_heading(level=6))
        self.assertEqual(self.cursor.focus.cell_id, before)

    def test_heading_nav_noop_outside_notebook_mode(self) -> None:
        self.cursor.focus_cell(self.session.cells[1].cell_id)
        self.cursor.enter_input_mode()
        self.assertIsNone(self.cursor.move_to_next_heading())
        self.assertIsNone(self.cursor.move_to_previous_heading())

    def test_empty_session_heading_nav_is_safe(self) -> None:
        bus = EventBus()
        empty = Session.new()
        cursor = NotebookCursor(empty, bus)
        self.assertIsNone(cursor.move_to_next_heading())
        self.assertIsNone(cursor.move_to_previous_heading())

    def test_list_headings_returns_outline(self) -> None:
        toc = self.cursor.list_headings()
        self.assertEqual(
            [(i, level, title) for i, level, title, _ in toc],
            [(0, 1, "Intro"), (2, 2, "Setup"), (4, 1, "Runs")],
        )
        # cell_ids line up with the session's actual cells.
        for i, _, _, cell_id in toc:
            self.assertEqual(cell_id, self.session.cells[i].cell_id)


class HeadingCreationTests(unittest.TestCase):
    """new_heading_cell adds a landmark without entering INPUT mode."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.session = Session.new()
        self.cursor = NotebookCursor(self.session, self.bus)

    def test_new_heading_cell_appends_and_stays_in_notebook(self) -> None:
        cell = self.cursor.new_heading_cell(2, "Setup")
        self.assertEqual(cell.kind, CellKind.HEADING)
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(self.cursor.focus.cell_id, cell.cell_id)
        self.assertEqual(self.session.cells[-1].cell_id, cell.cell_id)

    def test_new_heading_cell_publishes_cell_created(self) -> None:
        created: list[Event] = []
        self.bus.subscribe(EventType.CELL_CREATED, created.append)
        cell = self.cursor.new_heading_cell(1, "Intro")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].payload["cell_id"], cell.cell_id)

    def test_enter_input_mode_on_heading_is_noop(self) -> None:
        heading = self.cursor.new_heading_cell(1, "Intro")
        result = self.cursor.enter_input_mode()
        self.assertIsNone(result)
        self.assertEqual(self.cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(self.cursor.focus.cell_id, heading.cell_id)

    def test_duplicate_of_heading_cell_is_also_a_heading(self) -> None:
        self.cursor.new_heading_cell(2, "Setup")
        dup = self.cursor.duplicate_focused_cell()
        assert dup is not None
        self.assertEqual(dup.kind, CellKind.HEADING)
        self.assertEqual(dup.heading_level, 2)
        self.assertEqual(dup.heading_title, "Setup")


if __name__ == "__main__":
    unittest.main()
