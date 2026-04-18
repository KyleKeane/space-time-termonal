"""Unit tests for `python -m asat` (the CLI shim).

These tests exercise the small set of branches that only surface at
the CLI layer: the signpost stderr hint, `--version`, `--check`, and
the friendly non-TTY exit. The actual read-dispatch loop is covered
through Application tests.
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

from asat import __main__ as cli
from asat import __version__


class VersionFlagTests(unittest.TestCase):
    def test_version_prints_package_version_and_exits_zero(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = cli.main(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn(__version__, out.getvalue())


class SignpostHintTests(unittest.TestCase):
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


class CheckFlagTests(unittest.TestCase):
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
        self.assertIn("bindings", report)


class NonTTYTests(unittest.TestCase):
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


class MissingSessionPathTests(unittest.TestCase):
    """`--session /path/that/doesnt/exist.json` bootstraps a fresh session."""

    def test_missing_session_starts_fresh_and_saves_on_exit(self) -> None:
        import tempfile
        from pathlib import Path

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


class MissingBankPathTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
