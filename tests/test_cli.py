"""Unit tests for `python -m asat` (the CLI shim).

These tests exercise the small set of branches that only surface at
the CLI layer: the signpost stderr hint, `--version`, `--check`, and
the friendly non-TTY exit. The actual read-dispatch loop is covered
through Application tests.

Every CLI test inherits from `_AsatHomeIsolated` so a call to
`cli.main([...])` never writes the first-run sentinel into the
developer's real `~/.asat/` directory. See F46 in
docs/FEATURE_REQUESTS.md for the bug this protects against.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asat import __main__ as cli
from asat import __version__


class _AsatHomeIsolated(unittest.TestCase):
    """Point ASAT_HOME at a disposable tempdir for the duration of a test.

    Without this base class, any test that calls `cli.main([...])`
    without suppressing onboarding writes `first-run-done` into the
    developer's real home directory on the first run and shadows the
    bug on every subsequent run. Setting ASAT_HOME to a tempdir is
    sufficient because `_asat_home()` honours the env var.
    """

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.asat_home = Path(self._tempdir.name)
        self._prior_asat_home = os.environ.get("ASAT_HOME")
        os.environ["ASAT_HOME"] = str(self.asat_home)
        self.addCleanup(self._restore_asat_home)

    def _restore_asat_home(self) -> None:
        if self._prior_asat_home is None:
            os.environ.pop("ASAT_HOME", None)
        else:
            os.environ["ASAT_HOME"] = self._prior_asat_home


class VersionFlagTests(_AsatHomeIsolated):
    def test_version_prints_package_version_and_exits_zero(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = cli.main(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn(__version__, out.getvalue())


class SignpostHintTests(_AsatHomeIsolated):
    """`python -m asat` with no audio flag must nudge the user once."""

    def test_no_flags_prints_sink_hint_to_stderr(self) -> None:
        err = io.StringIO()
        out = io.StringIO()
        fake_keyboard = mock.MagicMock()
        fake_keyboard.read_key.return_value = None
        with mock.patch("asat.__main__.pick_default", return_value=fake_keyboard), \
             mock.patch("sys.stderr", err), \
             mock.patch("sys.stdout", out):
            rc = cli.main([])
        self.assertEqual(rc, 0)
        self.assertIn("in-memory sink", err.getvalue())
        self.assertIn("--live", err.getvalue())
        self.assertIn("--wav-dir", err.getvalue())

    def test_live_flag_suppresses_hint(self) -> None:
        err = io.StringIO()
        out = io.StringIO()
        fake_keyboard = mock.MagicMock()
        fake_keyboard.read_key.return_value = None
        with mock.patch("asat.__main__.pick_default", return_value=fake_keyboard), \
             mock.patch("sys.stderr", err), \
             mock.patch("sys.stdout", out):
            rc = cli.main(["--live"])
        self.assertEqual(rc, 0)
        self.assertNotIn("in-memory sink. Pass", err.getvalue())


class CheckFlagTests(_AsatHomeIsolated):
    def test_check_prints_summary_and_exits_without_reading_keys(self) -> None:
        out = io.StringIO()
        # pick_default must NOT be called; --check short-circuits.
        with mock.patch("asat.__main__.pick_default", side_effect=AssertionError), \
             mock.patch("sys.stdout", out):
            rc = cli.main(["--check"])
        self.assertEqual(rc, 0)
        report = out.getvalue()
        self.assertIn("asat", report)
        self.assertIn("platform", report)
        self.assertIn("sink", report)
        self.assertIn("runner", report)
        self.assertIn("bindings", report)

    def test_no_shared_shell_falls_back_to_process_runner(self) -> None:
        # `--no-shared-shell` opts out of the F60 persistent backend
        # so each cell runs in its own one-shot subprocess. The runner
        # line in `--check` is the user-visible signal that the flag
        # took effect.
        out = io.StringIO()
        with mock.patch("asat.__main__.pick_default", side_effect=AssertionError), \
             mock.patch("sys.stdout", out):
            rc = cli.main(["--check", "--no-shared-shell"])
        self.assertEqual(rc, 0)
        self.assertIn("runner         ProcessRunner", out.getvalue())


class NonTTYTests(_AsatHomeIsolated):
    """A non-interactive stdin produces a clean exit, not a traceback."""

    def test_non_tty_exits_with_code_2_and_friendly_message(self) -> None:
        from asat.keyboard import KeyboardNotAvailable

        err = io.StringIO()
        out = io.StringIO()
        raise_on_pick = mock.MagicMock(
            side_effect=KeyboardNotAvailable("needs a TTY")
        )
        with mock.patch("asat.__main__.pick_default", raise_on_pick), \
             mock.patch("sys.stderr", err), \
             mock.patch("sys.stdout", out):
            rc = cli.main(["--live"])
        self.assertEqual(rc, 2)
        self.assertIn("cannot start", err.getvalue())
        self.assertIn("needs a TTY", err.getvalue())


class MissingSessionPathTests(_AsatHomeIsolated):
    """`--session /path/that/doesnt/exist.json` bootstraps a fresh session."""

    def test_missing_session_starts_fresh_and_saves_on_exit(self) -> None:
        fake_keyboard = mock.MagicMock()
        fake_keyboard.read_key.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.json"
            self.assertFalse(path.exists())
            err = io.StringIO()
            out = io.StringIO()
            with mock.patch("asat.__main__.pick_default", return_value=fake_keyboard), \
                 mock.patch("sys.stderr", err), \
                 mock.patch("sys.stdout", out):
                rc = cli.main(["--live", "--session", str(path)])
            self.assertEqual(rc, 0)
            # File was created on exit (Application.close() path).
            self.assertTrue(path.exists())


class MissingBankPathTests(_AsatHomeIsolated):
    """`--bank /path/that/doesnt/exist.json` exits with a friendly error."""

    def test_missing_bank_exits_with_code_2_and_hint(self) -> None:
        err = io.StringIO()
        out = io.StringIO()
        with mock.patch("sys.stderr", err), \
             mock.patch("sys.stdout", out):
            rc = cli.main(["--bank", "/tmp/asat-no-such-bank.json"])
        self.assertEqual(rc, 2)
        self.assertIn("--bank", err.getvalue())
        self.assertIn("file not found", err.getvalue())


class AsatHomeHelperTests(unittest.TestCase):
    """Direct unit tests for the `_asat_home()` override helper.

    These do NOT inherit from `_AsatHomeIsolated` because they need to
    exercise the unset-env-var branch themselves.
    """

    def setUp(self) -> None:
        self._prior = os.environ.get("ASAT_HOME")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._prior is None:
            os.environ.pop("ASAT_HOME", None)
        else:
            os.environ["ASAT_HOME"] = self._prior

    def test_default_is_home_dot_asat_when_env_var_unset(self) -> None:
        os.environ.pop("ASAT_HOME", None)
        self.assertEqual(cli._asat_home(), Path.home() / ".asat")

    def test_env_var_override_takes_precedence(self) -> None:
        os.environ["ASAT_HOME"] = "/tmp/asat-custom-home"
        self.assertEqual(cli._asat_home(), Path("/tmp/asat-custom-home"))

    def test_empty_env_var_is_treated_as_unset(self) -> None:
        # An empty string is the usual shell shorthand for "no value";
        # honour Path.home() rather than the current directory.
        os.environ["ASAT_HOME"] = ""
        self.assertEqual(cli._asat_home(), Path.home() / ".asat")


class SentinelLocationTests(_AsatHomeIsolated):
    """End-to-end: `cli.main([])` writes the sentinel under ASAT_HOME."""

    def test_first_run_sentinel_lands_in_asat_home_not_real_home(self) -> None:
        err = io.StringIO()
        out = io.StringIO()
        fake_keyboard = mock.MagicMock()
        fake_keyboard.read_key.return_value = None
        sentinel = self.asat_home / "first-run-done"
        self.assertFalse(sentinel.exists())
        with mock.patch("asat.__main__.pick_default", return_value=fake_keyboard), \
             mock.patch("sys.stderr", err), \
             mock.patch("sys.stdout", out):
            rc = cli.main([])
        self.assertEqual(rc, 0)
        self.assertTrue(
            sentinel.exists(),
            f"sentinel should be written under ASAT_HOME ({self.asat_home}); "
            "if this test fails, the test suite may be polluting the real "
            "user's home directory — see F46 in docs/FEATURE_REQUESTS.md.",
        )


class WorkspaceResolutionTests(_AsatHomeIsolated):
    """`asat <dir>` / `asat <file.asatnb>` / `--init-workspace` map to
    a (Workspace, session_path) pair via `_resolve_workspace`."""

    def _parse(self, *argv: str):
        return cli._parse_args(list(argv))

    def test_no_args_returns_legacy_mode(self) -> None:
        args = self._parse()
        workspace, session_path = cli._resolve_workspace(args)
        self.assertIsNone(workspace)
        self.assertIsNone(session_path)

    def test_init_workspace_creates_layout_and_default_notebook(self) -> None:
        from asat.workspace import (
            DEFAULT_NOTEBOOK_NAME,
            WORKSPACE_NOTEBOOK_EXTENSION,
            Workspace,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fresh"
            args = self._parse("--init-workspace", str(root))
            workspace, session_path = cli._resolve_workspace(args)
            assert workspace is not None
            assert session_path is not None
            self.assertTrue(Workspace.is_workspace(workspace.root))
            self.assertEqual(
                session_path.name,
                DEFAULT_NOTEBOOK_NAME + WORKSPACE_NOTEBOOK_EXTENSION,
            )

    def test_init_workspace_refuses_existing_workspace(self) -> None:
        from asat.workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp:
            Workspace.init(tmp)
            args = self._parse("--init-workspace", tmp)
            with self.assertRaises(cli._FriendlyExit):
                cli._resolve_workspace(args)

    def test_directory_arg_loads_existing_workspace(self) -> None:
        from asat.workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            ws.new_notebook("only")
            args = self._parse(tmp)
            workspace, session_path = cli._resolve_workspace(args)
            assert workspace is not None
            assert session_path is not None
            self.assertEqual(workspace.root, ws.root)
            self.assertEqual(session_path.stem, "only")

    def test_directory_without_marker_fails_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._parse(tmp)
            with self.assertRaises(cli._FriendlyExit):
                cli._resolve_workspace(args)

    def test_notebook_name_resolves_within_workspace(self) -> None:
        from asat.workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            ws.new_notebook("alpha")
            ws.new_notebook("beta")
            args = self._parse(tmp, "alpha")
            workspace, session_path = cli._resolve_workspace(args)
            assert session_path is not None
            self.assertEqual(session_path.stem, "alpha")

    def test_unknown_notebook_name_fails_friendly(self) -> None:
        from asat.workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp:
            Workspace.init(tmp)
            args = self._parse(tmp, "missing")
            with self.assertRaises(cli._FriendlyExit):
                cli._resolve_workspace(args)

    def test_asatnb_file_arg_finds_enclosing_workspace(self) -> None:
        from asat.workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.init(tmp)
            path = ws.new_notebook("entry")
            args = self._parse(str(path))
            workspace, session_path = cli._resolve_workspace(args)
            assert workspace is not None
            assert session_path is not None
            self.assertEqual(workspace.root, ws.root)
            self.assertEqual(session_path, path.resolve())

    def test_asatnb_file_outside_any_workspace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stray = Path(tmp) / "lone.asatnb"
            stray.write_text("{}", encoding="utf-8")
            args = self._parse(str(stray))
            with self.assertRaises(cli._FriendlyExit):
                cli._resolve_workspace(args)


class MainLoopSupervisorTests(unittest.TestCase):
    """Verify the Never Crashes invariant at the ``_run`` loop layer."""

    def _make_app_stub(self, raise_on_key: Exception):
        """Return a minimal Application-like stub that raises on first key."""
        from asat.event_bus import EventBus
        from asat.keys import Key, Modifier

        class _AppStub:
            def __init__(self) -> None:
                self.bus = EventBus()
                self.running = True
                self.calls: list[Key] = []
                self._first_call = True

            def handle_key(self, key):
                self.calls.append(key)
                if self._first_call:
                    self._first_call = False
                    raise raise_on_key

            def drain_pending(self):
                return []

            def execute(self, cell_id):
                pass  # pragma: no cover

        return _AppStub()

    def test_exception_in_handle_key_is_caught_and_loop_continues(self) -> None:
        # Never Crashes: handle_key raising must not exit the loop.
        # The supervisor catches, publishes AUDIO_PIPELINE_FAILED,
        # logs, and moves on to the next key.
        from asat.events import EventType
        from asat.keyboard import ScriptedKeyboard
        from asat.keys import Key

        app = self._make_app_stub(RuntimeError("router boom"))
        observed_failures: list[dict] = []
        app.bus.subscribe(
            EventType.AUDIO_PIPELINE_FAILED,
            lambda event: observed_failures.append(dict(event.payload)),
        )
        keys = iter([Key.printable("a"), Key.printable("b")])
        keyboard = ScriptedKeyboard(keys)
        err = io.StringIO()

        with mock.patch("sys.stderr", err):
            cli._run(app, keyboard)

        # Both keys were delivered — the first one raised, the
        # second one still arrived at handle_key.
        self.assertEqual(len(app.calls), 2)
        # AUDIO_PIPELINE_FAILED was published with the error details.
        self.assertEqual(len(observed_failures), 1)
        self.assertEqual(observed_failures[0]["error_class"], "RuntimeError")
        self.assertIn("router boom", observed_failures[0]["error_message"])
        # A stderr line was written for sighted/developer debugging.
        self.assertIn("recovered from error", err.getvalue())
        self.assertIn("RuntimeError", err.getvalue())

    def test_keyboard_interrupt_still_propagates(self) -> None:
        # KeyboardInterrupt is the one exception that MUST propagate
        # so Ctrl+C behaves predictably. Verify the supervisor does
        # not swallow it.
        from asat.keyboard import ScriptedKeyboard
        from asat.keys import Key

        app = self._make_app_stub(KeyboardInterrupt())
        keys = iter([Key.printable("a")])
        keyboard = ScriptedKeyboard(keys)

        with self.assertRaises(KeyboardInterrupt):
            cli._run(app, keyboard)


if __name__ == "__main__":
    unittest.main()
