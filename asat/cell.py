"""Cell model: one input/output interaction in the notebook.

A Cell records a single command submission and the resulting output.
It is the atomic unit of the notebook workflow.

Mutation policy
---------------
Cell is the only non-frozen dataclass in the core data layer, and that
is deliberate: its state changes over an execution lifecycle
(PENDING -> RUNNING -> COMPLETED/FAILED/CANCELLED) and every mutation
needs to update `updated_at` atomically. To keep the rules simple:

* Only two callers are allowed to mutate a Cell:
    - ExecutionKernel (mark_running, mark_completed, mark_cancelled)
    - NotebookCursor  (update_command on edit-in-place)
* Every mutation goes through one of the `mark_*` / `update_command`
  methods so the timestamp and status stay consistent.
* Any consumer that wants a stable view while handling an event must
  call Cell.snapshot() to get a defensive copy that will not change
  under its feet when the original mutates later.

Fields are ordered so the most important identity information is read
first by a screen reader: id, command, timestamp, then outputs, then
status and lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from asat.common import new_id, utcnow


class CellStatus(str, Enum):
    """Lifecycle states for a Cell.

    PENDING: Created but not yet executed.
    RUNNING: Currently being executed by the kernel.
    COMPLETED: Finished with exit code zero.
    FAILED: Finished with a non-zero exit code or raised an error.
    CANCELLED: User cancelled execution before completion.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Cell:
    """A single input/output notebook cell.

    Use Cell.new(command) to construct a fresh cell. Direct construction
    is supported for deserialization but callers should prefer the
    factory method so that identifiers and timestamps are generated
    consistently.
    """

    cell_id: str
    command: str
    created_at: datetime
    updated_at: datetime
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    status: CellStatus = CellStatus.PENDING
    parent_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, command: str, parent_id: Optional[str] = None) -> "Cell":
        """Create a fresh pending cell for the given command.

        The parent_id is used when the user edits and re-runs a previous
        cell. It lets the session preserve the original cell as history
        while treating the new cell as its branch.
        """
        now = utcnow()
        return cls(
            cell_id=new_id(),
            command=command,
            created_at=now,
            updated_at=now,
            parent_id=parent_id,
        )

    def mark_running(self) -> None:
        """Transition this cell to the RUNNING state."""
        self.status = CellStatus.RUNNING
        self.updated_at = utcnow()

    def mark_completed(self, stdout: str, stderr: str, exit_code: int) -> None:
        """Record a completed execution and set status from the exit code."""
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.status = CellStatus.COMPLETED if exit_code == 0 else CellStatus.FAILED
        self.updated_at = utcnow()

    def mark_cancelled(self) -> None:
        """Record that the user cancelled this cell before completion."""
        self.status = CellStatus.CANCELLED
        self.updated_at = utcnow()

    def update_command(self, new_command: str) -> None:
        """Edit the input command and reset output-related state.

        Used when the user edits a previous cell in place rather than
        branching. Output fields are cleared so a stale result is never
        shown alongside an unexecuted command.
        """
        self.command = new_command
        self.stdout = ""
        self.stderr = ""
        self.exit_code = None
        self.status = CellStatus.PENDING
        self.updated_at = utcnow()

    def snapshot(self) -> "Cell":
        """Return a detached copy of this cell at the current moment.

        Subscribers that cache cell state from an event must use this
        rather than the original reference: the kernel may mutate the
        source Cell immediately after publishing the event, and the
        metadata dict is deep-copied so later edits do not leak in.
        """
        return Cell(
            cell_id=self.cell_id,
            command=self.command,
            created_at=self.created_at,
            updated_at=self.updated_at,
            stdout=self.stdout,
            stderr=self.stderr,
            exit_code=self.exit_code,
            status=self.status,
            parent_id=self.parent_id,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this cell to a JSON-compatible dictionary."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cell":
        """Rebuild a Cell from a dictionary previously produced by to_dict."""
        return cls(
            cell_id=data["cell_id"],
            command=data["command"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            exit_code=data.get("exit_code"),
            status=CellStatus(data.get("status", CellStatus.PENDING.value)),
            parent_id=data.get("parent_id"),
            metadata=dict(data.get("metadata", {})),
        )
