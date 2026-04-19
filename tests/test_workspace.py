"""Tests for asat.workspace — directory layout, notebook resolution, config I/O."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from asat.session import Session
from asat.workspace import (
    DEFAULT_NOTEBOOK_NAME,
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
    WORKSPACE_LOG_DIR,
    WORKSPACE_NOTEBOOKS_DIR,
    WORKSPACE_NOTEBOOK_EXTENSION,
    Workspace,
    WorkspaceConfig,
    WorkspaceError,
)


class WorkspaceInitTests(unittest.TestCase):

    def test_init_creates_the_expected_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            ws = Workspace.init(root)
            self.assertTrue(ws.root.exists())
            self.assertTrue(
                (ws.root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE).exists()
            )
            self.assertTrue(
                (ws.root / WORKSPACE_CONFIG_DIR / WORKSPACE_LOG_DIR).is_dir()
            )
            self.assertTrue((ws.root / WORKSPACE_NOTEBOOKS_DIR).is_dir())

    def test_init_rejects_existing_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Workspace.init(tmp)
            with self.assertRaises(WorkspaceError):
                Workspace.init(tmp)

    def test_init_writes_schema_version_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            data = json.loads(ws.config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertTrue(data["created_at"])
            self.assertIsNone(data["last_opened_notebook"])


class WorkspaceLoadTests(unittest.TestCase):

    def test_load_round_trips_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = Workspace.init(tmp)
            original.config.metadata["note"] = "hi"
            original.save_config()
            reopened = Workspace.load(tmp)
            self.assertEqual(reopened.config.metadata, {"note": "hi"})

    def test_load_missing_workspace_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkspaceError):
                Workspace.load(tmp)

    def test_is_workspace_is_false_for_plain_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(Workspace.is_workspace(tmp))

    def test_is_workspace_is_true_after_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Workspace.init(tmp)
            self.assertTrue(Workspace.is_workspace(tmp))

    def test_find_enclosing_walks_up_from_notebook_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            path = ws.new_notebook("nested")
            found = Workspace.find_enclosing(path)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found.root, ws.root)

    def test_find_enclosing_returns_none_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "not-a-workspace.txt"
            plain.write_text("nothing", encoding="utf-8")
            self.assertIsNone(Workspace.find_enclosing(plain))


class WorkspaceForwardCompatTests(unittest.TestCase):

    def test_unknown_config_keys_are_preserved_through_metadata(self) -> None:
        """An older ASAT reading a newer config must not crash.

        Unknown top-level keys are dropped (future fields own their
        own handling); anything already under ``metadata`` round-trips."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / WORKSPACE_CONFIG_DIR
            config_dir.mkdir(parents=True)
            (config_dir / WORKSPACE_LOG_DIR).mkdir()
            (Path(tmp) / WORKSPACE_NOTEBOOKS_DIR).mkdir()
            (config_dir / WORKSPACE_CONFIG_FILE).write_text(
                json.dumps(
                    {
                        "schema_version": 99,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "last_opened_notebook": None,
                        "metadata": {"future_toggle": True},
                        "future_only_key": "ignored",
                    }
                ),
                encoding="utf-8",
            )
            ws = Workspace.load(tmp)
            self.assertEqual(ws.config.schema_version, 99)
            self.assertEqual(ws.config.metadata, {"future_toggle": True})


class NotebookPathTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace.init(self._tmp.name)

    def test_bare_name_gets_extension(self) -> None:
        path = self.ws.notebook_path("ideas")
        self.assertEqual(path.suffix, WORKSPACE_NOTEBOOK_EXTENSION)
        self.assertEqual(path.parent, self.ws.notebooks_dir)

    def test_explicit_extension_is_respected(self) -> None:
        path = self.ws.notebook_path("ideas.asatnb")
        self.assertEqual(path.name, "ideas.asatnb")

    def test_relative_subpath_is_allowed(self) -> None:
        path = self.ws.notebook_path("sub/plan")
        self.assertEqual(path.parent.name, "sub")
        self.assertEqual(path.name, "plan" + WORKSPACE_NOTEBOOK_EXTENSION)

    def test_absolute_path_rejected(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.notebook_path("/etc/passwd")

    def test_path_escape_rejected(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.notebook_path("../../secret")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.notebook_path("")


class NewNotebookTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace.init(self._tmp.name)

    def test_new_notebook_writes_an_empty_session(self) -> None:
        path = self.ws.new_notebook("first")
        self.assertTrue(path.exists())
        session = Session.load(path)
        self.assertEqual(len(session.cells), 0)
        self.assertIsNone(session.cwd)

    def test_new_notebook_records_explicit_cwd(self) -> None:
        path = self.ws.new_notebook("with-cwd", cwd="/tmp/project")
        session = Session.load(path)
        self.assertEqual(session.cwd, "/tmp/project")

    def test_duplicate_rejected(self) -> None:
        self.ws.new_notebook("once")
        with self.assertRaises(WorkspaceError):
            self.ws.new_notebook("once")

    def test_list_notebooks_returns_sorted_paths(self) -> None:
        self.ws.new_notebook("zeta")
        self.ws.new_notebook("alpha")
        self.ws.new_notebook("mu")
        names = [p.stem for p in self.ws.list_notebooks()]
        self.assertEqual(names, ["alpha", "mu", "zeta"])

    def test_list_notebooks_ignores_non_asatnb_files(self) -> None:
        self.ws.new_notebook("real")
        (self.ws.notebooks_dir / "README.txt").write_text(
            "ignored", encoding="utf-8"
        )
        paths = self.ws.list_notebooks()
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].stem, "real")


class ResolveCwdTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace.init(self._tmp.name)

    def test_unset_cwd_falls_through_to_workspace_root(self) -> None:
        session = Session.new()
        self.assertEqual(self.ws.resolve_cwd(session), self.ws.root)

    def test_explicit_cwd_wins(self) -> None:
        session = Session.new()
        session.cwd = self._tmp.name  # Use an existing real directory
        self.assertEqual(
            self.ws.resolve_cwd(session), Path(self._tmp.name).resolve()
        )


class DefaultNotebookTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace.init(self._tmp.name)

    def test_default_creates_a_notebook_when_empty(self) -> None:
        path = self.ws.default_notebook()
        self.assertTrue(path.exists())
        self.assertEqual(path.stem, DEFAULT_NOTEBOOK_NAME)

    def test_default_returns_single_existing(self) -> None:
        created = self.ws.new_notebook("only-one")
        self.assertEqual(self.ws.default_notebook(), created)

    def test_default_prefers_last_opened_when_valid(self) -> None:
        first = self.ws.new_notebook("first")
        second = self.ws.new_notebook("second")
        self.ws.set_last_opened(second)
        self.assertEqual(self.ws.default_notebook(), second)
        # Sanity: without the pointer, ``default_notebook`` returns the
        # first in sorted order.
        self.ws.config.last_opened_notebook = None
        self.ws.save_config()
        self.assertEqual(self.ws.default_notebook(), first)

    def test_last_opened_pointer_ignored_when_file_vanishes(self) -> None:
        kept = self.ws.new_notebook("kept")
        vanishing = self.ws.new_notebook("vanishing")
        self.ws.set_last_opened(vanishing)
        vanishing.unlink()
        # Falls through to the sole remaining notebook.
        self.assertEqual(self.ws.default_notebook(), kept)


class SetLastOpenedTests(unittest.TestCase):

    def test_stores_path_relative_to_notebooks_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            path = ws.new_notebook("sub/one")
            ws.set_last_opened(path)
            reopened = Workspace.load(tmp)
            self.assertEqual(
                reopened.config.last_opened_notebook,
                str(Path("sub") / ("one" + WORKSPACE_NOTEBOOK_EXTENSION)),
            )

    def test_outside_path_clears_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            ws.config.last_opened_notebook = "stale.asatnb"
            ws.save_config()
            ws.set_last_opened(Path(tmp) / "outside.txt")
            self.assertIsNone(ws.config.last_opened_notebook)


class SessionCwdSerializationTests(unittest.TestCase):
    """The cwd field is additive; old session JSON (no `cwd` key)
    must still load and come back with ``cwd is None``."""

    def test_missing_cwd_defaults_to_none(self) -> None:
        payload = {
            "session_id": "old",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "active_cell_id": None,
            "metadata": {},
            "cells": [],
        }
        session = Session.from_dict(payload)
        self.assertIsNone(session.cwd)

    def test_cwd_round_trips_through_dict(self) -> None:
        session = Session.new()
        session.cwd = "/opt/project"
        restored = Session.from_dict(session.to_dict())
        self.assertEqual(restored.cwd, "/opt/project")


if __name__ == "__main__":
    unittest.main()
