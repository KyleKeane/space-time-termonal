"""Notebook cursor and focus state.

The NotebookCursor is the single source of truth for "where in the
session is the user looking?". It owns a FocusState, knows how to
walk the cell list, and manages the input buffer that the user types
into while editing a cell.

The cursor publishes FOCUS_CHANGED events whenever its state actually
changes. Idempotent operations (e.g., move_up at the top) publish no
event so subscribers can rely on FOCUS_CHANGED meaning a real
transition happened.

The cursor never executes commands. When the user submits, the
cursor commits the input buffer into the focused cell and returns
the cell: some higher-level controller is responsible for handing
it to the execution kernel. This keeps the input layer completely
decoupled from subprocess management.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from asat.cell import Cell
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.session import Session


class FocusMode(str, Enum):
    """Where keystrokes are currently directed.

    NOTEBOOK: arrow keys navigate between cells.
    INPUT: keystrokes edit the focused cell's command buffer.
    OUTPUT: keystrokes walk through lines of the focused cell's
        output. Reserved for a future phase; included here so code
        can already branch on it safely.
    """

    NOTEBOOK = "notebook"
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True)
class FocusState:
    """Snapshot of the cursor at a point in time.

    mode: current focus mode.
    cell_id: id of the cell being looked at, or None if the session
        is empty and nothing is focused.
    input_buffer: the text currently being typed when in INPUT mode.
        Empty in other modes.
    """

    mode: FocusMode = FocusMode.NOTEBOOK
    cell_id: Optional[str] = None
    input_buffer: str = ""


class NotebookCursor:
    """Maintains the current focus within a Session.

    The cursor owns a FocusState and publishes FOCUS_CHANGED events
    when it moves or when the input buffer changes. Methods return
    helpful values (the newly focused Cell, or True/False to signal
    whether a no-op occurred) so callers can decide what to voice.
    """

    SOURCE = "notebook"

    def __init__(self, session: Session, bus: EventBus) -> None:
        """Attach the cursor to a session and an event bus."""
        self._session = session
        self._bus = bus
        initial_cell_id = (
            session.active_cell_id
            if session.active_cell_id is not None
            else (session.cells[0].cell_id if session.cells else None)
        )
        self._state = FocusState(mode=FocusMode.NOTEBOOK, cell_id=initial_cell_id)
        if initial_cell_id is not None and session.active_cell_id is None:
            session.set_active(initial_cell_id)

    @property
    def focus(self) -> FocusState:
        """Return the current focus state."""
        return self._state

    def move_up(self) -> Optional[Cell]:
        """Move to the previous cell. Returns the new cell or None."""
        return self._move(delta=-1)

    def move_down(self) -> Optional[Cell]:
        """Move to the next cell. Returns the new cell or None."""
        return self._move(delta=+1)

    def move_to_top(self) -> Optional[Cell]:
        """Focus the first cell in the session."""
        if not self._session.cells:
            return None
        return self.focus_cell(self._session.cells[0].cell_id)

    def move_to_bottom(self) -> Optional[Cell]:
        """Focus the last cell in the session."""
        if not self._session.cells:
            return None
        return self.focus_cell(self._session.cells[-1].cell_id)

    def focus_cell(self, cell_id: str) -> Cell:
        """Focus a specific cell by id, returning to notebook mode."""
        cell = self._session.get_cell(cell_id)
        self._session.set_active(cell_id)
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=cell_id,
                input_buffer="",
            )
        )
        return cell

    def new_cell(self, command: str = "") -> Cell:
        """Append a fresh cell, focus it, and enter input mode."""
        cell = Cell.new(command)
        self._session.add_cell(cell)
        self._session.set_active(cell.cell_id)
        self._transition(
            FocusState(
                mode=FocusMode.INPUT,
                cell_id=cell.cell_id,
                input_buffer=command,
            )
        )
        return cell

    def enter_input_mode(self) -> Optional[FocusState]:
        """Switch from notebook to input mode on the focused cell."""
        if self._state.cell_id is None:
            return None
        cell = self._session.get_cell(self._state.cell_id)
        self._transition(
            FocusState(
                mode=FocusMode.INPUT,
                cell_id=cell.cell_id,
                input_buffer=cell.command,
            )
        )
        return self._state

    def exit_input_mode(self) -> Optional[FocusState]:
        """Commit the input buffer to the focused cell and return to notebook mode."""
        if self._state.mode != FocusMode.INPUT or self._state.cell_id is None:
            return None
        self._commit_buffer_to_cell()
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=self._state.cell_id,
                input_buffer="",
            )
        )
        return self._state

    def view_output_mode(self) -> Optional[FocusState]:
        """Switch from notebook to output mode on the focused cell.

        OUTPUT mode is where line-level navigation through a cell's
        captured output happens. The cursor simply flips its mode;
        attaching an OutputCursor to the appropriate buffer is the
        caller's job.
        """
        if self._state.mode != FocusMode.NOTEBOOK or self._state.cell_id is None:
            return None
        self._transition(
            FocusState(
                mode=FocusMode.OUTPUT,
                cell_id=self._state.cell_id,
                input_buffer="",
            )
        )
        return self._state

    def exit_output_mode(self) -> Optional[FocusState]:
        """Return to notebook mode from output mode."""
        if self._state.mode != FocusMode.OUTPUT or self._state.cell_id is None:
            return None
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=self._state.cell_id,
                input_buffer="",
            )
        )
        return self._state

    def insert_character(self, character: str) -> None:
        """Append a character to the input buffer while in INPUT mode."""
        if self._state.mode != FocusMode.INPUT:
            return
        if len(character) != 1:
            raise ValueError("insert_character expects exactly one character")
        self._transition(replace(self._state, input_buffer=self._state.input_buffer + character))

    def backspace(self) -> None:
        """Delete the last character from the input buffer."""
        if self._state.mode != FocusMode.INPUT:
            return
        if not self._state.input_buffer:
            return
        self._transition(replace(self._state, input_buffer=self._state.input_buffer[:-1]))

    def submit(self) -> Optional[Cell]:
        """Commit the input buffer and return the cell ready for execution.

        The cursor transitions back to NOTEBOOK mode. Returns the cell
        so that a controller layer can hand it to the execution kernel.
        Returns None if called outside INPUT mode or with no focused cell.
        """
        if self._state.mode != FocusMode.INPUT or self._state.cell_id is None:
            return None
        cell = self._commit_buffer_to_cell()
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=cell.cell_id,
                input_buffer="",
            )
        )
        return cell

    def _move(self, delta: int) -> Optional[Cell]:
        """Move the cursor by a signed offset within the cell list."""
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        if not self._session.cells:
            return None
        current_id = self._state.cell_id
        if current_id is None:
            return None
        current_index = self._session.index_of(current_id)
        target_index = current_index + delta
        if target_index < 0 or target_index >= len(self._session.cells):
            return None
        return self.focus_cell(self._session.cells[target_index].cell_id)

    def _commit_buffer_to_cell(self) -> Cell:
        """Write the current input buffer to the focused cell."""
        assert self._state.cell_id is not None
        cell = self._session.get_cell(self._state.cell_id)
        if cell.command != self._state.input_buffer:
            cell.update_command(self._state.input_buffer)
        return cell

    def _transition(self, new_state: FocusState) -> None:
        """Replace the focus state and publish an event if it changed."""
        if new_state == self._state:
            return
        old_state = self._state
        self._state = new_state
        self._bus.publish(
            Event(
                event_type=EventType.FOCUS_CHANGED,
                payload={
                    "old_mode": old_state.mode.value,
                    "new_mode": new_state.mode.value,
                    "old_cell_id": old_state.cell_id,
                    "new_cell_id": new_state.cell_id,
                    "input_buffer": new_state.input_buffer,
                },
                source=self.SOURCE,
            )
        )
