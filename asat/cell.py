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


class CellKind(str, Enum):
    """What flavour of cell this is.

    COMMAND cells are the executable input/output units the notebook
    was originally built around. HEADING cells are structural
    landmarks that anchor NVDA-style heading navigation (F61). TEXT
    cells carry prose inside a notebook — the "we train for ten
    epochs because earlier runs overfit" annotation that the heading
    work couldn't hold (F27). Future kinds (terminal, rich-media)
    plug in here without rewriting the session/cursor/render layers.
    """

    COMMAND = "command"
    HEADING = "heading"
    TEXT = "text"


MIN_HEADING_LEVEL = 1
MAX_HEADING_LEVEL = 6


@dataclass
class Cell:
    """A single notebook cell.

    Use Cell.new(command) for an executable command cell, or
    Cell.new_heading(level, title) for a heading landmark. Direct
    construction is supported for deserialization but callers should
    prefer the factory methods so identifiers, timestamps, and kind
    stay consistent.
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
    kind: CellKind = CellKind.COMMAND
    heading_level: Optional[int] = None
    heading_title: Optional[str] = None
    text: Optional[str] = None
    collapsed: bool = False

    def __post_init__(self) -> None:
        if self.kind is CellKind.HEADING:
            if self.heading_level is None or not (
                MIN_HEADING_LEVEL <= self.heading_level <= MAX_HEADING_LEVEL
            ):
                raise ValueError(
                    f"heading cell requires heading_level in "
                    f"{MIN_HEADING_LEVEL}..{MAX_HEADING_LEVEL}"
                )
            if self.heading_title is None or not self.heading_title.strip():
                raise ValueError("heading cell requires a non-empty heading_title")
        else:
            if self.heading_level is not None or self.heading_title is not None:
                raise ValueError(
                    "heading_level / heading_title only apply to HEADING cells"
                )
        if self.kind is CellKind.TEXT:
            if self.text is None or not self.text.strip():
                raise ValueError("text cell requires a non-empty text body")
        else:
            if self.text is not None:
                raise ValueError("text only applies to TEXT cells")
        if self.collapsed and self.kind is not CellKind.HEADING:
            raise ValueError("collapsed only applies to HEADING cells")

    @classmethod
    def new(cls, command: str, parent_id: Optional[str] = None) -> "Cell":
        """Create a fresh pending command cell.

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

    @classmethod
    def new_text(cls, text: str) -> "Cell":
        """Create a fresh prose/text cell (F27).

        Text cells carry narrative alongside a notebook's commands and
        headings. They are non-executable (`is_executable` returns
        False) so the kernel, worker, and auto-advance paths skip them.
        """
        if not text.strip():
            raise ValueError("text cell requires a non-empty body")
        now = utcnow()
        return cls(
            cell_id=new_id(),
            command="",
            created_at=now,
            updated_at=now,
            status=CellStatus.COMPLETED,
            kind=CellKind.TEXT,
            text=text,
        )

    @classmethod
    def new_heading(cls, level: int, title: str) -> "Cell":
        """Create a fresh heading landmark cell at the given level (1..6)."""
        if not (MIN_HEADING_LEVEL <= level <= MAX_HEADING_LEVEL):
            raise ValueError(
                f"heading level must be {MIN_HEADING_LEVEL}..{MAX_HEADING_LEVEL}"
            )
        if not title.strip():
            raise ValueError("heading title must be non-empty")
        now = utcnow()
        return cls(
            cell_id=new_id(),
            command="",
            created_at=now,
            updated_at=now,
            status=CellStatus.COMPLETED,
            kind=CellKind.HEADING,
            heading_level=level,
            heading_title=title,
        )

    @property
    def is_heading(self) -> bool:
        return self.kind is CellKind.HEADING

    @property
    def is_text(self) -> bool:
        return self.kind is CellKind.TEXT

    @property
    def is_executable(self) -> bool:
        """True iff this cell represents runnable work.

        Headings are landmarks, not commands; the kernel and worker
        must skip them. Future read-only cell kinds fall through here
        too.
        """
        return self.kind is CellKind.COMMAND

    def _require_executable(self, op: str) -> None:
        if not self.is_executable:
            raise ValueError(f"cannot {op} on a non-executable cell (kind={self.kind.value})")

    def mark_running(self) -> None:
        """Transition this cell to the RUNNING state."""
        self._require_executable("mark_running")
        self.status = CellStatus.RUNNING
        self.updated_at = utcnow()

    def mark_completed(self, stdout: str, stderr: str, exit_code: int) -> None:
        """Record a completed execution and set status from the exit code."""
        self._require_executable("mark_completed")
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.status = CellStatus.COMPLETED if exit_code == 0 else CellStatus.FAILED
        self.updated_at = utcnow()

    def mark_cancelled(self) -> None:
        """Record that the user cancelled this cell before completion."""
        self._require_executable("mark_cancelled")
        self.status = CellStatus.CANCELLED
        self.updated_at = utcnow()

    def update_command(self, new_command: str) -> None:
        """Edit the input command and reset output-related state.

        Used when the user edits a previous cell in place rather than
        branching. Output fields are cleared so a stale result is never
        shown alongside an unexecuted command.
        """
        self._require_executable("update_command")
        self.command = new_command
        self.stdout = ""
        self.stderr = ""
        self.exit_code = None
        self.status = CellStatus.PENDING
        self.updated_at = utcnow()

    def update_text(self, new_text: str) -> None:
        """Edit the prose of a text cell in place."""
        if not self.is_text:
            raise ValueError("update_text only applies to text cells")
        if not new_text.strip():
            raise ValueError("text cell requires a non-empty body")
        self.text = new_text
        self.updated_at = utcnow()

    def update_heading(self, level: int, title: str) -> None:
        """Edit the level/title of a heading cell in place."""
        if not self.is_heading:
            raise ValueError("update_heading only applies to heading cells")
        if not (MIN_HEADING_LEVEL <= level <= MAX_HEADING_LEVEL):
            raise ValueError(
                f"heading level must be {MIN_HEADING_LEVEL}..{MAX_HEADING_LEVEL}"
            )
        if not title.strip():
            raise ValueError("heading title must be non-empty")
        self.heading_level = level
        self.heading_title = title
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
            kind=self.kind,
            heading_level=self.heading_level,
            heading_title=self.heading_title,
            text=self.text,
            collapsed=self.collapsed,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this cell to a JSON-compatible dictionary."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        data["status"] = self.status.value
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cell":
        """Rebuild a Cell from a dictionary previously produced by to_dict.

        Missing `kind` / `heading_level` / `heading_title` / `text`
        default to a COMMAND cell so sessions written before F61 /
        F27 load cleanly.
        """
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
            kind=CellKind(data.get("kind", CellKind.COMMAND.value)),
            heading_level=data.get("heading_level"),
            heading_title=data.get("heading_title"),
            text=data.get("text"),
            collapsed=bool(data.get("collapsed", False)),
        )
