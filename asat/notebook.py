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

from asat.cell import Cell, CellKind
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.outline import enclosing_heading_index, scope_range, visible_indices
from asat.session import Session, SessionError


class FocusMode(str, Enum):
    """Where keystrokes are currently directed.

    NOTEBOOK: arrow keys navigate between cells.
    INPUT: keystrokes edit the focused cell's command buffer.
    OUTPUT: keystrokes walk through lines of the focused cell's
        output. Reserved for a future phase; included here so code
        can already branch on it safely.
    SETTINGS: keystrokes drive the SoundBank editor (SettingsEditor
        via SettingsController). Entered from NOTEBOOK by a dedicated
        shortcut or the `:settings` meta-command.
    """

    NOTEBOOK = "notebook"
    INPUT = "input"
    OUTPUT = "output"
    SETTINGS = "settings"


@dataclass(frozen=True)
class FocusState:
    """Snapshot of the cursor at a point in time.

    mode: current focus mode.
    cell_id: id of the cell being looked at, or None if the session
        is empty and nothing is focused.
    input_buffer: the text currently being typed when in INPUT mode.
        Empty in other modes.
    cursor_position: the caret offset into input_buffer (0 == before
        the first character, len(buffer) == after the last). Only
        meaningful in INPUT mode; kept at 0 in other modes. Always
        satisfies 0 <= cursor_position <= len(input_buffer).
    """

    mode: FocusMode = FocusMode.NOTEBOOK
    cell_id: Optional[str] = None
    input_buffer: str = ""
    cursor_position: int = 0


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
        # F4 history-recall browse state. ``_history_index`` is None
        # whenever we're not actively walking history; an integer means
        # the buffer currently shows ``session.command_history[idx]``.
        # ``_history_pre_browse`` remembers what the user had typed
        # before they reached for Up so Down past the most-recent entry
        # can restore it.
        self._history_index: Optional[int] = None
        self._history_pre_browse: str = ""

    @property
    def focus(self) -> FocusState:
        """Return the current focus state."""
        return self._state

    @property
    def session(self) -> Session:
        """Return the underlying Session (read-only handle)."""
        return self._session

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
                cursor_position=0,
            )
        )
        return cell

    def new_cell(self, command: str = "") -> Cell:
        """Append a fresh cell, focus it, and enter input mode."""
        cell = Cell.new(command)
        self._session.add_cell(cell)
        self._session.set_active(cell.cell_id)
        self._publish_cell_created(cell, len(self._session.cells) - 1)
        self._transition(
            FocusState(
                mode=FocusMode.INPUT,
                cell_id=cell.cell_id,
                input_buffer=command,
                cursor_position=len(command),
            )
        )
        return cell

    def new_heading_cell(self, level: int, title: str) -> Cell:
        """Append a heading landmark, focus it, and stay in NOTEBOOK mode.

        Headings are not executable, so there's no input buffer and no
        transition into INPUT — they live alongside command cells purely
        as structural anchors for NVDA-style heading navigation.
        """
        cell = Cell.new_heading(level, title)
        self._session.add_cell(cell)
        self._session.set_active(cell.cell_id)
        self._publish_cell_created(cell, len(self._session.cells) - 1)
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=cell.cell_id,
                input_buffer="",
                cursor_position=0,
            )
        )
        return cell

    def new_text_cell(self, text: str) -> Cell:
        """Append a prose/text cell, focus it, and stay in NOTEBOOK mode.

        Text cells carry narrative the heading and command kinds can't
        (F27). Like headings they are non-executable and live in
        NOTEBOOK mode as read-only landmarks.
        """
        cell = Cell.new_text(text)
        self._session.add_cell(cell)
        self._session.set_active(cell.cell_id)
        self._publish_cell_created(cell, len(self._session.cells) - 1)
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=cell.cell_id,
                input_buffer="",
                cursor_position=0,
            )
        )
        return cell

    def move_to_next_heading(self, level: Optional[int] = None) -> Optional[Cell]:
        """Jump forward to the next heading. None level = any level.

        NVDA convention: always step past the current cell, so
        repeatedly pressing the shortcut cycles through headings one
        at a time. Returns the landed cell, or None if there is no
        next heading matching the filter (in which case the cursor
        does not move).
        """
        return self._move_to_heading(direction=+1, level=level)

    def move_to_previous_heading(self, level: Optional[int] = None) -> Optional[Cell]:
        """Jump backward to the previous heading. None level = any level."""
        return self._move_to_heading(direction=-1, level=level)

    def _move_to_heading(self, direction: int, level: Optional[int]) -> Optional[Cell]:
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        cells = self._session.cells
        if not cells:
            return None
        if self._state.cell_id is None:
            start_index = -1 if direction > 0 else len(cells)
        else:
            start_index = self._session.index_of(self._state.cell_id)
        index = start_index + direction
        while 0 <= index < len(cells):
            candidate = cells[index]
            if candidate.kind is CellKind.HEADING and (
                level is None or candidate.heading_level == level
            ):
                return self.focus_cell(candidate.cell_id)
            index += direction
        return None

    def list_headings(self) -> list[tuple[int, int, str, str]]:
        """Return the notebook's TOC: list of (index, level, title, cell_id).

        Used by the `:toc` meta-command and any future outline view.
        Empty when the session has no heading cells.
        """
        out: list[tuple[int, int, str, str]] = []
        for i, cell in enumerate(self._session.cells):
            if cell.kind is CellKind.HEADING:
                assert cell.heading_level is not None
                assert cell.heading_title is not None
                out.append((i, cell.heading_level, cell.heading_title, cell.cell_id))
        return out

    def move_to_next_parent_heading(self) -> Optional[Cell]:
        """Jump forward to the next heading shallower than the current scope (F27).

        "Current scope" is the focused heading's own level when the focus
        is a heading, or the nearest preceding heading's level when the
        focus is a regular cell. Returns None (cursor unchanged) when
        there is no enclosing scope or no shallower heading ahead.
        """
        return self._move_to_parent_heading(direction=+1)

    def move_to_previous_parent_heading(self) -> Optional[Cell]:
        """Jump backward to the previous heading shallower than the current scope (F27)."""
        return self._move_to_parent_heading(direction=-1)

    def _current_scope_level(self) -> Optional[int]:
        if self._state.cell_id is None:
            return None
        cells = self._session.cells
        try:
            index = self._session.index_of(self._state.cell_id)
        except ValueError:
            return None
        focused = cells[index]
        if focused.kind is CellKind.HEADING and focused.heading_level is not None:
            return focused.heading_level
        j = index - 1
        while j >= 0:
            cand = cells[j]
            if cand.kind is CellKind.HEADING and cand.heading_level is not None:
                return cand.heading_level
            j -= 1
        return None

    def toggle_fold_focused_heading(self) -> Optional[bool]:
        """Collapse / expand the focused heading (F27).

        Legal only from NOTEBOOK mode on a heading cell. Returns the
        new `collapsed` value (True = just folded, False = just
        unfolded) or None if the focus is not a foldable heading.
        Publishes `OUTLINE_FOLDED` or `OUTLINE_UNFOLDED` with the
        heading's metadata and the count of hidden cells.
        """
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        cell_id = self._state.cell_id
        if cell_id is None:
            return None
        cells = self._session.cells
        try:
            index = self._session.index_of(cell_id)
        except ValueError:
            return None
        target = cells[index]
        if target.kind is not CellKind.HEADING or target.heading_level is None:
            return None
        start, end = scope_range(cells, index)
        # A heading with no body (unit span) has nothing to fold or
        # unfold; skip the toggle so the state and events stay
        # meaningful.
        if end - start <= 1:
            return None
        target.collapsed = not target.collapsed
        event_type = (
            EventType.OUTLINE_FOLDED if target.collapsed else EventType.OUTLINE_UNFOLDED
        )
        publish_event(
            self._bus,
            event_type,
            {
                "cell_id": target.cell_id,
                "heading_level": target.heading_level,
                "heading_title": target.heading_title,
                "cell_count": end - start - 1,
            },
            source="cursor",
        )
        return target.collapsed

    def select_heading_scope(self) -> Optional[list[Cell]]:
        """Return the cells that belong to the focused cell's heading scope (F27).

        "Scope" is the section governed by the nearest enclosing
        heading — the heading itself plus every following cell up to
        (but not including) the next same-or-shallower heading. When
        the focus is a heading, its own section is returned. When the
        focus is a non-heading cell, the enclosing heading's section
        is returned. Returns None if there is no focused cell or no
        enclosing heading.

        The returned list is a fresh list (callers may mutate it
        freely) but the cells inside are the session's actual Cell
        instances — same sharing contract as `Session.cells`. For a
        defensive copy, call `.snapshot()` on each cell.
        """
        if self._state.cell_id is None:
            return None
        cells = self._session.cells
        try:
            index = self._session.index_of(self._state.cell_id)
        except ValueError:
            return None
        heading_index = enclosing_heading_index(cells, index)
        if heading_index is None:
            return None
        start, end = scope_range(cells, heading_index)
        return list(cells[start:end])

    def _move_to_parent_heading(self, direction: int) -> Optional[Cell]:
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        scope_level = self._current_scope_level()
        if scope_level is None:
            return None
        cells = self._session.cells
        if not cells:
            return None
        if self._state.cell_id is None:
            start_index = -1 if direction > 0 else len(cells)
        else:
            start_index = self._session.index_of(self._state.cell_id)
        index = start_index + direction
        while 0 <= index < len(cells):
            candidate = cells[index]
            if (
                candidate.kind is CellKind.HEADING
                and candidate.heading_level is not None
                and candidate.heading_level < scope_level
            ):
                return self.focus_cell(candidate.cell_id)
            index += direction
        return None

    def delete_focused_cell(self) -> Optional[Cell]:
        """Remove the focused cell and land on a sensible neighbor.

        Legal only from NOTEBOOK mode. Returns the removed Cell so a
        caller can restore it from a payload if needed. When the last
        cell is removed the cursor lands on None / the empty session;
        otherwise it focuses the cell that slid into the removed
        position, or the new last cell if the removed one was at the
        tail. Publishes CELL_REMOVED so the sound bank's cue fires.
        """
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        cell_id = self._state.cell_id
        if cell_id is None:
            return None
        removed_index = self._session.index_of(cell_id)
        removed = self._session.remove_cell(cell_id)
        publish_event(
            self._bus,
            EventType.CELL_REMOVED,
            {
                "cell_id": removed.cell_id,
                "command": removed.command,
                "index": removed_index,
            },
            source=self.SOURCE,
        )
        if not self._session.cells:
            self._transition(FocusState(mode=FocusMode.NOTEBOOK, cell_id=None))
            return removed
        next_index = min(removed_index, len(self._session.cells) - 1)
        self.focus_cell(self._session.cells[next_index].cell_id)
        return removed

    def duplicate_focused_cell(self) -> Optional[Cell]:
        """Insert a copy of the focused cell immediately after it.

        The duplicate is a fresh PENDING cell carrying the same command
        text, so re-running it does not inherit the original's stale
        output. Cursor focuses the duplicate in NOTEBOOK mode (not
        INPUT — users who want to edit can press Enter next). Publishes
        CELL_CREATED.
        """
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        cell_id = self._state.cell_id
        if cell_id is None:
            return None
        source = self._session.get_cell(cell_id)
        if source.is_heading:
            assert source.heading_level is not None
            assert source.heading_title is not None
            duplicate = Cell.new_heading(source.heading_level, source.heading_title)
        elif source.is_text:
            assert source.text is not None
            duplicate = Cell.new_text(source.text)
        else:
            duplicate = Cell.new(source.command)
        target_index = self._session.index_of(cell_id) + 1
        self._session.add_cell(duplicate, position=target_index)
        self._publish_cell_created(duplicate, target_index)
        self.focus_cell(duplicate.cell_id)
        return duplicate

    def move_focused_cell(self, delta: int) -> bool:
        """Shift the focused cell up (-1) or down (+1) within the list.

        Returns True when the move succeeded. Returns False when the
        cursor is not in NOTEBOOK mode, no cell is focused, or the cell
        is already at the requested boundary. Publishes CELL_MOVED with
        the old and new indices so the sound engine can play its cue.
        """
        if self._state.mode != FocusMode.NOTEBOOK:
            return False
        cell_id = self._state.cell_id
        if cell_id is None:
            return False
        old_index = self._session.index_of(cell_id)
        new_index = old_index + delta
        if new_index < 0 or new_index >= len(self._session.cells):
            return False
        self._session.move_cell(cell_id, new_index)
        publish_event(
            self._bus,
            EventType.CELL_MOVED,
            {
                "cell_id": cell_id,
                "old_index": old_index,
                "new_index": new_index,
            },
            source=self.SOURCE,
        )
        return True

    def _publish_cell_created(self, cell: Cell, index: int) -> None:
        """Publish CELL_CREATED with the canonical payload shape."""
        publish_event(
            self._bus,
            EventType.CELL_CREATED,
            {
                "cell_id": cell.cell_id,
                "command": cell.command,
                "index": index,
            },
            source=self.SOURCE,
        )

    def enter_input_mode(self) -> Optional[FocusState]:
        """Switch from notebook to input mode on the focused cell.

        The caret lands at the end of the existing command so typing
        continues to append the way the user expects. Heading cells
        have no command buffer, so this is a no-op on them.
        """
        if self._state.cell_id is None:
            return None
        cell = self._session.get_cell(self._state.cell_id)
        if not cell.is_executable:
            return None
        self._clear_history_browse()
        self._transition(
            FocusState(
                mode=FocusMode.INPUT,
                cell_id=cell.cell_id,
                input_buffer=cell.command,
                cursor_position=len(cell.command),
            )
        )
        return self._state

    def exit_input_mode(self) -> Optional[FocusState]:
        """Commit the input buffer to the focused cell and return to notebook mode."""
        if self._state.mode != FocusMode.INPUT or self._state.cell_id is None:
            return None
        self._commit_buffer_to_cell()
        self._clear_history_browse()
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=self._state.cell_id,
                input_buffer="",
                cursor_position=0,
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
                cursor_position=0,
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
                cursor_position=0,
            )
        )
        return self._state

    def enter_settings_mode(self) -> FocusState:
        """Switch to SETTINGS mode. Legal from any notebook state."""
        self._transition(
            FocusState(
                mode=FocusMode.SETTINGS,
                cell_id=self._state.cell_id,
                input_buffer="",
                cursor_position=0,
            )
        )
        return self._state

    def exit_settings_mode(self) -> Optional[FocusState]:
        """Return to NOTEBOOK mode from SETTINGS mode."""
        if self._state.mode != FocusMode.SETTINGS:
            return None
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=self._state.cell_id,
                input_buffer="",
                cursor_position=0,
            )
        )
        return self._state

    def abandon_input_mode(self) -> Optional[FocusState]:
        """Exit INPUT mode without committing the buffer to the cell.

        Meta-commands (e.g. `:settings`) consume the buffer themselves,
        so the normal exit path (which writes the buffer back into the
        focused cell) would leave stale text behind. abandon_input_mode
        discards the buffer and returns to NOTEBOOK.
        """
        if self._state.mode != FocusMode.INPUT or self._state.cell_id is None:
            return None
        self._clear_history_browse()
        self._transition(
            FocusState(
                mode=FocusMode.NOTEBOOK,
                cell_id=self._state.cell_id,
                input_buffer="",
                cursor_position=0,
            )
        )
        return self._state

    def reset_input_buffer(self) -> None:
        """Clear the input buffer while staying in INPUT mode.

        Used by "ambient" meta-commands like `:help` and `:save` that
        consume the typed buffer (so the literal `:help` text does not
        linger) but leave the user exactly where they were, ready to
        keep typing. No FOCUS_CHANGED is published — this matches the
        contract for every other buffer-only mutation (typing a key,
        Backspace).
        """
        if self._state.mode != FocusMode.INPUT:
            return
        if not self._state.input_buffer:
            return
        self._clear_history_browse()
        self._transition(replace(self._state, input_buffer="", cursor_position=0))

    def insert_character(self, character: str) -> None:
        """Insert a character at the current caret position.

        The caret advances by one so subsequent inserts chain naturally.
        """
        if self._state.mode != FocusMode.INPUT:
            return
        if len(character) != 1:
            raise ValueError("insert_character expects exactly one character")
        self._clear_history_browse()
        position = self._state.cursor_position
        buffer = self._state.input_buffer
        new_buffer = buffer[:position] + character + buffer[position:]
        self._transition(
            replace(self._state, input_buffer=new_buffer, cursor_position=position + 1)
        )

    def backspace(self) -> None:
        """Delete the character immediately before the caret."""
        if self._state.mode != FocusMode.INPUT:
            return
        position = self._state.cursor_position
        if position == 0:
            return
        self._clear_history_browse()
        buffer = self._state.input_buffer
        new_buffer = buffer[: position - 1] + buffer[position:]
        self._transition(
            replace(self._state, input_buffer=new_buffer, cursor_position=position - 1)
        )

    def cursor_left(self) -> None:
        """Move the caret one character to the left; clamp at start."""
        if self._state.mode != FocusMode.INPUT:
            return
        if self._state.cursor_position == 0:
            return
        self._transition(replace(self._state, cursor_position=self._state.cursor_position - 1))

    def cursor_right(self) -> None:
        """Move the caret one character to the right; clamp at end."""
        if self._state.mode != FocusMode.INPUT:
            return
        if self._state.cursor_position >= len(self._state.input_buffer):
            return
        self._transition(replace(self._state, cursor_position=self._state.cursor_position + 1))

    def cursor_home(self) -> None:
        """Jump the caret to the start of the input buffer."""
        if self._state.mode != FocusMode.INPUT:
            return
        if self._state.cursor_position == 0:
            return
        self._transition(replace(self._state, cursor_position=0))

    def cursor_end(self) -> None:
        """Jump the caret to the end of the input buffer."""
        if self._state.mode != FocusMode.INPUT:
            return
        end = len(self._state.input_buffer)
        if self._state.cursor_position == end:
            return
        self._transition(replace(self._state, cursor_position=end))

    def delete_forward(self) -> None:
        """Delete the character at the caret (Delete key)."""
        if self._state.mode != FocusMode.INPUT:
            return
        position = self._state.cursor_position
        buffer = self._state.input_buffer
        if position >= len(buffer):
            return
        self._clear_history_browse()
        new_buffer = buffer[:position] + buffer[position + 1 :]
        self._transition(replace(self._state, input_buffer=new_buffer))

    def delete_word_left(self) -> None:
        """Delete the whitespace-delimited word preceding the caret."""
        if self._state.mode != FocusMode.INPUT:
            return
        position = self._state.cursor_position
        if position == 0:
            return
        buffer = self._state.input_buffer
        # Skip trailing whitespace, then skip the word itself.
        i = position
        while i > 0 and buffer[i - 1].isspace():
            i -= 1
        while i > 0 and not buffer[i - 1].isspace():
            i -= 1
        if i == position:
            return
        self._clear_history_browse()
        new_buffer = buffer[:i] + buffer[position:]
        self._transition(replace(self._state, input_buffer=new_buffer, cursor_position=i))

    def delete_to_start(self) -> None:
        """Delete everything from the start of the buffer up to the caret."""
        if self._state.mode != FocusMode.INPUT:
            return
        position = self._state.cursor_position
        if position == 0:
            return
        self._clear_history_browse()
        new_buffer = self._state.input_buffer[position:]
        self._transition(replace(self._state, input_buffer=new_buffer, cursor_position=0))

    def delete_to_end(self) -> None:
        """Delete everything from the caret to the end of the buffer."""
        if self._state.mode != FocusMode.INPUT:
            return
        position = self._state.cursor_position
        buffer = self._state.input_buffer
        if position >= len(buffer):
            return
        self._clear_history_browse()
        new_buffer = buffer[:position]
        self._transition(replace(self._state, input_buffer=new_buffer))

    def submit(self) -> Optional[Cell]:
        """Commit the input buffer and return the cell ready for execution.

        The cursor auto-advances to a fresh empty INPUT cell after a
        non-empty submit from the last cell in the session, so the
        user can immediately type their next command (the REPL-like
        behaviour documented in FEATURE_REQUESTS.md F11). Submitting
        an empty buffer, or re-running an already-executed middle
        cell, still lands in NOTEBOOK on the submitted cell — the
        user was specifically editing that cell and shouldn't have a
        new empty cell wedged in after it.

        Returns the cell that was submitted so a controller layer can
        hand it to the execution kernel. Returns None if called
        outside INPUT mode or with no focused cell.
        """
        if self._state.mode != FocusMode.INPUT or self._state.cell_id is None:
            return None
        cell = self._commit_buffer_to_cell()
        self._session.record_command(cell.command)
        self._clear_history_browse()
        should_autoadvance = (
            bool(cell.command.strip())
            and self._session.cells
            and self._session.cells[-1].cell_id == cell.cell_id
        )
        if should_autoadvance:
            new_cell = Cell.new("")
            self._session.add_cell(new_cell)
            self._session.set_active(new_cell.cell_id)
            self._publish_cell_created(new_cell, len(self._session.cells) - 1)
            self._transition(
                FocusState(
                    mode=FocusMode.INPUT,
                    cell_id=new_cell.cell_id,
                    input_buffer="",
                    cursor_position=0,
                )
            )
        else:
            self._transition(
                FocusState(
                    mode=FocusMode.NOTEBOOK,
                    cell_id=cell.cell_id,
                    input_buffer="",
                    cursor_position=0,
                )
            )
        return cell

    def set_input_buffer(self, text: str) -> None:
        """Replace the input buffer with ``text`` and park the caret at its end.

        Public primitive for code that wants to programmatically
        overwrite what the user is typing — the F4 history-recall path
        is the first caller. INPUT mode only; silent no-op elsewhere.
        Always clears any in-progress history browse so subsequent
        Up/Down restart from the most-recent entry.
        """
        if self._state.mode != FocusMode.INPUT:
            return
        self._clear_history_browse()
        self._transition(replace(self._state, input_buffer=text, cursor_position=len(text)))

    def history_previous(self) -> bool:
        """Step backward through ``session.command_history``.

        On the first Up the cursor remembers whatever the user had
        already typed (the "draft") so a later Down past the most
        recent entry can restore it. Returns True when the buffer
        actually changed; False when there's nothing to recall (not in
        INPUT mode, history is empty, or already at the oldest entry).
        """
        if self._state.mode != FocusMode.INPUT:
            return False
        history = self._session.command_history
        if not history:
            return False
        if self._history_index is None:
            self._history_pre_browse = self._state.input_buffer
            self._history_index = len(history)
        if self._history_index <= 0:
            return False
        self._history_index -= 1
        text = history[self._history_index]
        self._transition(replace(self._state, input_buffer=text, cursor_position=len(text)))
        return True

    def history_next(self) -> bool:
        """Step forward through history; restore the draft past the most recent.

        No-op (returns False) when the cursor isn't currently browsing
        history — Down is meaningless until Up has put us somewhere to
        come back from. Stepping forward from the most-recent entry
        clears the browse state and restores whatever the user had
        typed before they reached for Up.
        """
        if self._state.mode != FocusMode.INPUT:
            return False
        if self._history_index is None:
            return False
        history = self._session.command_history
        next_index = self._history_index + 1
        if next_index >= len(history):
            text = self._history_pre_browse
            self._clear_history_browse()
            self._transition(
                replace(self._state, input_buffer=text, cursor_position=len(text))
            )
            return True
        self._history_index = next_index
        text = history[next_index]
        self._transition(replace(self._state, input_buffer=text, cursor_position=len(text)))
        return True

    def _clear_history_browse(self) -> None:
        """Forget any in-progress history walk and the draft we'd restored."""
        self._history_index = None
        self._history_pre_browse = ""

    def _move(self, delta: int) -> Optional[Cell]:
        """Move the cursor by a signed offset within the cell list.

        Cells hidden by a collapsed heading (F27) are skipped — pressing
        Up/Down from the collapsed heading lands on the next visible
        cell, not on the children hidden inside the scope.
        """
        if self._state.mode != FocusMode.NOTEBOOK:
            return None
        cells = self._session.cells
        if not cells:
            return None
        current_id = self._state.cell_id
        if current_id is None:
            return None
        current_index = self._session.index_of(current_id)
        visible = visible_indices(cells)
        if current_index in visible:
            pos = visible.index(current_index)
        else:
            # Focus is hidden inside a collapsed scope — snap to the
            # enclosing heading, then apply the delta from there.
            heading_idx = enclosing_heading_index(cells, current_index)
            if heading_idx is None or heading_idx not in visible:
                return None
            pos = visible.index(heading_idx)
        target_pos = pos + delta
        if target_pos < 0 or target_pos >= len(visible):
            return None
        return self.focus_cell(cells[visible[target_pos]].cell_id)

    def _commit_buffer_to_cell(self) -> Cell:
        """Write the current input buffer to the focused cell."""
        assert self._state.cell_id is not None
        cell = self._session.get_cell(self._state.cell_id)
        if cell.command != self._state.input_buffer:
            cell.update_command(self._state.input_buffer)
        return cell

    def _transition(self, new_state: FocusState) -> None:
        """Replace the focus state and publish an event if it changed.

        Buffer-and-caret-only deltas (typing, deleting, and moving the
        caret within the input buffer) are intentionally silent. They
        would otherwise fire FOCUS_CHANGED per keystroke and drown the
        sound bank's `focus_shift` cue and the terminal trace's
        `[input #…]` banner in noise. Consumers that care about text
        or caret edits subscribe to ACTION_INVOKED instead (actions
        `insert_character`, `backspace`, `cursor_left`, `cursor_right`,
        etc., or the richer shortcuts like `delete_word_left`).
        """
        if new_state == self._state:
            return
        old_state = self._state
        mode_changed = old_state.mode != new_state.mode
        cell_changed = old_state.cell_id != new_state.cell_id
        if not mode_changed and not cell_changed:
            # Buffer-only change: update state but stay silent.
            self._state = new_state
            return
        if mode_changed:
            transition = "mode"
        elif cell_changed:
            transition = "cell"
        else:
            transition = "buffer"
        self._state = new_state
        command = ""
        kind_value = CellKind.COMMAND.value
        heading_level: Optional[int] = None
        heading_title: Optional[str] = None
        text: Optional[str] = None
        if new_state.cell_id is not None:
            try:
                focused = self._session.get_cell(new_state.cell_id)
                command = focused.command
                kind_value = focused.kind.value
                heading_level = focused.heading_level
                heading_title = focused.heading_title
                text = focused.text
            except SessionError:
                command = ""
        publish_event(
            self._bus,
            EventType.FOCUS_CHANGED,
            {
                "old_mode": old_state.mode.value,
                "new_mode": new_state.mode.value,
                "old_cell_id": old_state.cell_id,
                "new_cell_id": new_state.cell_id,
                "input_buffer": new_state.input_buffer,
                "transition": transition,
                "command": command,
                "kind": kind_value,
                "heading_level": heading_level,
                "heading_title": heading_title,
                "text": text,
            },
            source=self.SOURCE,
        )
