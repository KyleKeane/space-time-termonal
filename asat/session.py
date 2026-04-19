"""Session model: the notebook-level container of cells.

A Session owns an ordered list of Cells, a cursor pointing at the
currently focused cell, and the bookkeeping needed to reorder, remove,
and serialize them. The Session is deliberately passive: it never
executes commands or produces audio. Later phases call into the Session
from the execution kernel, input router, and audio engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from asat.cell import Cell
from asat.common import new_id, utcnow


class SessionError(Exception):
    """Raised when a Session operation is given an invalid argument."""


@dataclass
class Session:
    """An ordered collection of notebook cells.

    Use Session.new() for a fresh session. Direct construction is used
    by from_dict during deserialization. All mutation methods update the
    session's updated_at timestamp so persistence layers can detect
    dirty state.
    """

    session_id: str
    created_at: datetime
    updated_at: datetime
    cells: list[Cell] = field(default_factory=list)
    active_cell_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Per-notebook working directory. When ``None`` the workspace (if
    # any) falls through to its own root; when set it overrides the
    # workspace so one project can mix notebooks that run in different
    # sub-directories. Stored as a string for portable JSON; resolved
    # by ``Workspace.resolve_cwd`` at open time.
    cwd: Optional[str] = None
    # Ordered list of every non-empty command submitted in this
    # session, oldest first. Powers F4 history recall (Up/Down in INPUT
    # mode). Consecutive duplicates are collapsed at append time so a
    # user re-running the same command doesn't have to walk past it
    # repeatedly. Persisted with the session so resuming preserves the
    # walk.
    command_history: list[str] = field(default_factory=list)

    @classmethod
    def new(cls) -> "Session":
        """Create a fresh empty session."""
        now = utcnow()
        return cls(session_id=new_id(), created_at=now, updated_at=now)

    def __len__(self) -> int:
        """Return the number of cells currently in the session."""
        return len(self.cells)

    def __iter__(self) -> Iterator[Cell]:
        """Iterate over cells in notebook order."""
        return iter(self.cells)

    def add_cell(self, cell: Cell, position: Optional[int] = None) -> Cell:
        """Insert a cell at the given position, or append if position is None.

        Returns the inserted cell so callers can chain. Raises SessionError
        if a cell with the same id already exists, since duplicated ids
        would corrupt lookups and ordering.
        """
        if self._index_or_none(cell.cell_id) is not None:
            raise SessionError(f"Cell {cell.cell_id} already exists in session")
        if position is None:
            self.cells.append(cell)
        else:
            self._check_position(position, allow_end=True)
            self.cells.insert(position, cell)
        self.updated_at = utcnow()
        return cell

    def remove_cell(self, cell_id: str) -> Cell:
        """Remove and return the cell with the given id.

        Clears active_cell_id if it pointed at the removed cell.
        """
        index = self._require_index(cell_id)
        removed = self.cells.pop(index)
        if self.active_cell_id == cell_id:
            self.active_cell_id = None
        self.updated_at = utcnow()
        return removed

    def move_cell(self, cell_id: str, new_position: int) -> None:
        """Move a cell to a new position within the session.

        new_position is interpreted against the list after the cell has
        been removed, matching standard list.insert semantics. Positions
        outside [0, len - 1] raise SessionError.
        """
        current = self._require_index(cell_id)
        self._check_position(new_position, allow_end=False)
        cell = self.cells.pop(current)
        self.cells.insert(new_position, cell)
        self.updated_at = utcnow()

    def get_cell(self, cell_id: str) -> Cell:
        """Return the cell with the given id or raise SessionError."""
        index = self._require_index(cell_id)
        return self.cells[index]

    def index_of(self, cell_id: str) -> int:
        """Return the position of the cell with the given id."""
        return self._require_index(cell_id)

    def set_active(self, cell_id: Optional[str]) -> None:
        """Set the focused cell. Pass None to clear focus.

        Raises SessionError if the id is not present in the session.
        """
        if cell_id is not None:
            self._require_index(cell_id)
        self.active_cell_id = cell_id
        self.updated_at = utcnow()

    def active_cell(self) -> Optional[Cell]:
        """Return the currently focused cell, or None if no cell is focused."""
        if self.active_cell_id is None:
            return None
        return self.get_cell(self.active_cell_id)

    def next_cell(self, cell_id: str) -> Optional[Cell]:
        """Return the cell immediately after cell_id, or None at the end."""
        index = self._require_index(cell_id)
        if index + 1 >= len(self.cells):
            return None
        return self.cells[index + 1]

    def previous_cell(self, cell_id: str) -> Optional[Cell]:
        """Return the cell immediately before cell_id, or None at the start."""
        index = self._require_index(cell_id)
        if index == 0:
            return None
        return self.cells[index - 1]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session to a JSON-compatible dictionary."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "active_cell_id": self.active_cell_id,
            "metadata": dict(self.metadata),
            "cwd": self.cwd,
            "command_history": list(self.command_history),
            "cells": [cell.to_dict() for cell in self.cells],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """Rebuild a session from a dictionary previously produced by to_dict."""
        return cls(
            session_id=data["session_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            cells=[Cell.from_dict(item) for item in data.get("cells", [])],
            active_cell_id=data.get("active_cell_id"),
            metadata=dict(data.get("metadata", {})),
            cwd=data.get("cwd"),
            command_history=list(data.get("command_history", [])),
        )

    def record_command(self, command: str) -> bool:
        """Append ``command`` to the history if it's worth recalling.

        Returns True if the command was actually appended. Empty /
        whitespace-only commands are dropped, and a command identical
        to the most recent entry is collapsed (so the user doesn't
        walk past `pytest` ten times to reach the previous edit).
        """
        stripped = command.strip()
        if not stripped:
            return False
        if self.command_history and self.command_history[-1] == command:
            return False
        self.command_history.append(command)
        self.updated_at = utcnow()
        return True

    def save(self, path: Path | str) -> None:
        """Write the session as pretty-printed JSON to the given path."""
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "Session":
        """Read a session from a JSON file previously written by save."""
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def _index_or_none(self, cell_id: str) -> Optional[int]:
        """Return the index of the given cell id, or None if absent."""
        for index, cell in enumerate(self.cells):
            if cell.cell_id == cell_id:
                return index
        return None

    def _require_index(self, cell_id: str) -> int:
        """Return the index of the given cell id or raise SessionError."""
        index = self._index_or_none(cell_id)
        if index is None:
            raise SessionError(f"Cell {cell_id} not found in session")
        return index

    def _check_position(self, position: int, allow_end: bool) -> None:
        """Validate that position is a legal insertion or move target.

        allow_end permits position == len(cells), which is valid for
        insertion but not for moving an existing cell.
        """
        upper = len(self.cells) if allow_end else max(len(self.cells) - 1, 0)
        if position < 0 or position > upper:
            raise SessionError(f"Position {position} is out of range")
