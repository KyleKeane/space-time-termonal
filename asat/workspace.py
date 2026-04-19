"""Workspace: per-project root directory that groups notebooks and state.

A workspace is an ordinary directory on disk marked by a
`.asat/config.json` file. Everything ASAT keeps per-project
lives beneath the workspace root:

    <workspace_root>/
        .asat/
            config.json        — WorkspaceConfig (schema_version, …)
            log/               — reserved for F63 event-log files
        notebooks/
            <name>.asatnb      — one per notebook, JSON content

The file format for a notebook is the existing ``Session`` JSON
(``Session.to_dict`` / ``Session.from_dict``); the ``.asatnb``
extension is a stable marker so editors can associate a viewer
and so ``Workspace.list_notebooks`` can filter by suffix rather
than sniffing content.

Notebooks record their own working directory in ``Session.cwd``.
When the field is ``None`` the workspace root is used so a fresh
notebook "just works" in the project it was created in;
overriding lets one workspace mix a ``~/repo/backend`` notebook
with a ``~/repo/frontend`` one without chdir-ing by hand.

This module does no event publishing and takes no locks — it is
pure filesystem I/O so tests can drive it with a ``tempfile``
and the ``Application`` can layer event plumbing on top.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from asat.common import utcnow
from asat.session import Session

WORKSPACE_CONFIG_DIR = ".asat"
WORKSPACE_CONFIG_FILE = "config.json"
WORKSPACE_LOG_DIR = "log"
WORKSPACE_NOTEBOOKS_DIR = "notebooks"
WORKSPACE_NOTEBOOK_EXTENSION = ".asatnb"
WORKSPACE_SCHEMA_VERSION = 1
DEFAULT_NOTEBOOK_NAME = "default"


class WorkspaceError(Exception):
    """Raised when a workspace operation is given invalid state."""


@dataclass
class WorkspaceConfig:
    """Persisted metadata for a workspace directory.

    ``schema_version`` lets a future ASAT detect an old file and
    either migrate it or refuse cleanly. ``last_opened_notebook``
    is a path *relative to* ``<root>/notebooks`` so moving the
    workspace to a new disk does not invalidate the pointer.
    """

    schema_version: int = WORKSPACE_SCHEMA_VERSION
    created_at: str = ""
    last_opened_notebook: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict."""
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "last_opened_notebook": self.last_opened_notebook,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkspaceConfig":
        """Rebuild a config from a to_dict() snapshot.

        Unknown future fields are ignored rather than raising so
        an older ASAT reading a newer file degrades to the subset
        it understands. Missing fields take the dataclass default.
        """
        return cls(
            schema_version=int(
                data.get("schema_version", WORKSPACE_SCHEMA_VERSION)
            ),
            created_at=str(data.get("created_at", "")),
            last_opened_notebook=data.get("last_opened_notebook"),
            metadata=dict(data.get("metadata", {})),
        )


class Workspace:
    """A project directory containing one or more notebooks."""

    def __init__(self, root: Path, config: WorkspaceConfig) -> None:
        """Attach to an existing workspace directory on disk."""
        self.root = Path(root).resolve()
        self.config = config

    @classmethod
    def init(cls, root: Path | str) -> "Workspace":
        """Create the directory structure for a new workspace.

        ``root`` is created if it does not exist. A pre-existing
        ``<root>/.asat/config.json`` raises ``WorkspaceError`` —
        callers that want to open an existing workspace should use
        ``load`` instead; callers that want init-or-open should
        check ``is_workspace`` first.
        """
        root_path = Path(root).resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        config_dir = root_path / WORKSPACE_CONFIG_DIR
        config_path = config_dir / WORKSPACE_CONFIG_FILE
        if config_path.exists():
            raise WorkspaceError(
                f"Workspace already initialised at {root_path}"
            )
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / WORKSPACE_LOG_DIR).mkdir(parents=True, exist_ok=True)
        (root_path / WORKSPACE_NOTEBOOKS_DIR).mkdir(
            parents=True, exist_ok=True
        )
        config = WorkspaceConfig(created_at=utcnow().isoformat())
        workspace = cls(root_path, config)
        workspace.save_config()
        return workspace

    @classmethod
    def load(cls, root: Path | str) -> "Workspace":
        """Open an existing workspace. Raises ``WorkspaceError`` if missing."""
        root_path = Path(root).resolve()
        config_path = root_path / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        if not config_path.exists():
            raise WorkspaceError(
                f"No workspace at {root_path} "
                f"(expected {config_path})"
            )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        config = WorkspaceConfig.from_dict(data)
        return cls(root_path, config)

    @classmethod
    def is_workspace(cls, path: Path | str) -> bool:
        """True iff ``path`` is an initialised workspace directory."""
        candidate = Path(path)
        return (
            candidate.is_dir()
            and (candidate / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE).exists()
        )

    @classmethod
    def find_enclosing(cls, start: Path | str) -> Optional["Workspace"]:
        """Walk up from ``start`` looking for a workspace marker.

        Returns the nearest workspace that contains ``start`` or
        ``None`` when no ancestor is a workspace. Useful for
        ``asat <file.asatnb>`` — the CLI can resolve the file's
        workspace from the notebook path without asking the user.
        """
        current = Path(start).resolve()
        if current.is_file():
            current = current.parent
        for candidate in [current, *current.parents]:
            if cls.is_workspace(candidate):
                return cls.load(candidate)
        return None

    @property
    def config_path(self) -> Path:
        """Path to ``<root>/.asat/config.json``."""
        return self.root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE

    @property
    def notebooks_dir(self) -> Path:
        """Path to ``<root>/notebooks``."""
        return self.root / WORKSPACE_NOTEBOOKS_DIR

    @property
    def log_dir(self) -> Path:
        """Path to ``<root>/.asat/log``. Reserved for F63."""
        return self.root / WORKSPACE_CONFIG_DIR / WORKSPACE_LOG_DIR

    def list_notebooks(self) -> tuple[Path, ...]:
        """Return every ``.asatnb`` in the notebooks/ dir, sorted by name."""
        if not self.notebooks_dir.exists():
            return ()
        entries = sorted(
            p
            for p in self.notebooks_dir.iterdir()
            if p.is_file() and p.suffix == WORKSPACE_NOTEBOOK_EXTENSION
        )
        return tuple(entries)

    def notebook_path(self, name: str) -> Path:
        """Resolve a notebook identifier to an absolute path.

        Accepts a bare stem (``"ideas"``), an explicit filename
        (``"ideas.asatnb"``), or a path relative to the workspace's
        notebooks directory (``"sub/plan.asatnb"``). Paths that
        escape ``notebooks_dir`` raise ``WorkspaceError`` so a
        malicious ``../../../etc/passwd`` cannot be written
        through this API.
        """
        if not name:
            raise WorkspaceError("notebook name cannot be empty")
        candidate = Path(name)
        if candidate.is_absolute():
            raise WorkspaceError(
                f"notebook name must be relative to the workspace: {name}"
            )
        if candidate.suffix != WORKSPACE_NOTEBOOK_EXTENSION:
            candidate = candidate.with_suffix(
                WORKSPACE_NOTEBOOK_EXTENSION
            )
        target = (self.notebooks_dir / candidate).resolve()
        if not _is_inside(target, self.notebooks_dir.resolve()):
            raise WorkspaceError(
                f"notebook path {name!r} escapes the workspace"
            )
        return target

    def new_notebook(
        self,
        name: str,
        *,
        cwd: Optional[Path | str] = None,
    ) -> Path:
        """Create an empty notebook file inside the workspace.

        ``cwd`` is recorded on the new session so opening the
        notebook later chdir's into the project directory (or an
        explicit override). Pass ``None`` to inherit the workspace
        root at open time. Raises ``WorkspaceError`` when a file
        already exists at the resolved path so callers do not
        silently clobber prior work.
        """
        path = self.notebook_path(name)
        if path.exists():
            raise WorkspaceError(f"notebook already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        session = Session.new()
        if cwd is not None:
            session.cwd = str(Path(cwd))
        session.save(path)
        return path

    def resolve_cwd(self, session: Session) -> Path:
        """Return the working directory a session should run in.

        Per-notebook cwd wins; falls back to the workspace root so
        a notebook with no explicit cwd inherits the project's
        directory. Used by ``Application`` to ``chdir`` on open.
        """
        if session.cwd:
            return Path(session.cwd).expanduser().resolve()
        return self.root

    def save_config(self) -> None:
        """Write ``config.json`` back to disk."""
        payload = json.dumps(self.config.to_dict(), indent=2)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(payload, encoding="utf-8")

    def set_last_opened(self, notebook_path: Path | str) -> None:
        """Record a notebook as "last opened" and persist.

        Stored relative to ``notebooks_dir`` so a workspace that
        moves to another machine still resolves cleanly. Writes
        ``None`` back when the path lies outside the workspace
        rather than silently losing the pointer.
        """
        candidate = Path(notebook_path).resolve()
        try:
            relative = candidate.relative_to(self.notebooks_dir.resolve())
        except ValueError:
            self.config.last_opened_notebook = None
        else:
            self.config.last_opened_notebook = str(relative)
        self.save_config()

    def default_notebook(self) -> Path:
        """Return a notebook to open when the user just ran ``asat <dir>``.

        Preference order: the configured ``last_opened_notebook``
        if it still exists, then the single existing notebook if
        there is exactly one, then a freshly created
        ``default.asatnb``. The caller is responsible for publishing
        whatever event(s) explain the choice to the user.
        """
        if self.config.last_opened_notebook:
            candidate = self.notebooks_dir / self.config.last_opened_notebook
            if candidate.exists():
                return candidate
        existing = self.list_notebooks()
        if len(existing) == 1:
            return existing[0]
        if existing:
            return existing[0]
        return self.new_notebook(DEFAULT_NOTEBOOK_NAME)


def _is_inside(candidate: Path, root: Path) -> bool:
    """Return True iff ``candidate`` is ``root`` or lives beneath it."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
