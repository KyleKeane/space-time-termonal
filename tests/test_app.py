"""Unit tests for the Application wiring class.

These tests exercise the end-to-end pipeline that the CLI entry point
drives, but with a scripted key stream instead of a real keyboard and
a MemorySink instead of a live speaker. Every test is headless.
"""

from __future__ import annotations

import sys
import unittest
from typing import Optional

from asat import keys as kc
from asat.app import Application
from asat.audio_sink import MemorySink
from asat.cell import CellStatus
from asat.events import Event, EventType
from asat.execution import ExecutionResult
from asat.keys import Key, Modifier
from asat.notebook import FocusMode


class StubRunner:
    """A ProcessRunner stand-in for fast Application tests."""

    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self._result = ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=False,
        )
        self.last_command: Optional[str] = None

    def run(self, request, stdout_handler=None, stderr_handler=None):
        self.last_command = request.command
        if stdout_handler is not None and self._result.stdout:
            for line in self._result.stdout.splitlines():
                stdout_handler(line)
        if stderr_handler is not None and self._result.stderr:
            for line in self._result.stderr.splitlines():
                stderr_handler(line)
        return self._result


def _type(app: Application, text: str) -> None:
    """Feed each printable character of `text` as a Key."""
    for character in text:
        app.handle_key(Key.printable(character))


class ApplicationBuildTests(unittest.TestCase):
    def test_build_seeds_a_fresh_session_with_one_input_cell(self) -> None:
        app = Application.build()
        self.assertEqual(len(app.session), 1)
        self.assertEqual(app.cursor.focus.mode, FocusMode.INPUT)

    def test_build_respects_an_existing_session(self) -> None:
        from asat.session import Session

        session = Session.new()
        app = Application.build(session=session)
        self.assertIs(app.session, session)
        # An externally-supplied session is used as-is; no bootstrap cell
        # is created on the user's behalf.
        self.assertEqual(len(app.session), 0)
        self.assertEqual(app.cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_default_sink_is_memory(self) -> None:
        app = Application.build()
        self.assertIsInstance(app.sink, MemorySink)


class ApplicationSubmissionTests(unittest.TestCase):
    def test_typing_and_submit_executes_a_cell(self) -> None:
        sink = MemorySink()
        app = Application.build(sink=sink)
        app.kernel._runner = StubRunner(stdout="hi\n", exit_code=0)

        _type(app, "echo hi")
        app.handle_key(kc.ENTER)
        pending = app.drain_pending()
        self.assertEqual(len(pending), 1)
        app.execute(pending[0])

        cell = app.session.get_cell(pending[0])
        self.assertEqual(cell.command, "echo hi")
        self.assertEqual(cell.status, CellStatus.COMPLETED)
        self.assertEqual(cell.exit_code, 0)
        # MemorySink accumulates something for the narrations that fired.
        self.assertGreater(len(sink.buffers), 0)

    def test_submit_without_input_does_not_enqueue(self) -> None:
        app = Application.build()
        app.handle_key(kc.ENTER)
        self.assertEqual(app.drain_pending(), [])


class ApplicationMetaCommandTests(unittest.TestCase):
    def test_quit_meta_command_clears_running(self) -> None:
        app = Application.build()
        self.assertTrue(app.running)
        _type(app, ":quit")
        app.handle_key(kc.ENTER)
        self.assertFalse(app.running)
        # :quit does not count as a cell submission.
        self.assertEqual(app.drain_pending(), [])

    def test_quit_does_not_leak_the_meta_string_into_a_cell(self) -> None:
        app = Application.build()
        starting_command = app.session.cells[0].command
        _type(app, ":quit")
        app.handle_key(kc.ENTER)
        self.assertEqual(app.session.cells[0].command, starting_command)


class ApplicationPersistenceTests(unittest.TestCase):
    def test_close_persists_session_when_path_given(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            app = Application.build(session_path=path)
            _type(app, "ls")
            app.handle_key(kc.ESCAPE)  # commit buffer back to the cell
            app.close()
            self.assertTrue(path.exists())


class ApplicationSessionEventTests(unittest.TestCase):
    """Verify the session lifecycle events fire at the right boundaries.

    SESSION_CREATED fires inside `build()`, before any caller can
    subscribe directly on the Application's bus, so we witness it
    through a `RecordingSink` that passively receives every narration
    the SoundEngine produces for the SESSION_CREATED binding in the
    default bank. SESSION_SAVED is easier: it fires inside `close()`,
    which callers can hook before invoking.
    """

    def test_build_records_a_narration_for_session_created(self) -> None:
        # The default bank binds SESSION_CREATED to a system-voice
        # cue, so the sink receives at least one buffer during build.
        sink = MemorySink()
        Application.build(sink=sink)
        self.assertGreater(
            len(sink.buffers),
            0,
            "SoundEngine produced no buffer for SESSION_CREATED; the "
            "startup narration has probably gone silent.",
        )

    def test_close_publishes_session_saved_when_path_is_set(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            app = Application.build(session_path=path)
            seen: list[dict] = []
            app.bus.subscribe(
                EventType.SESSION_SAVED,
                lambda e: seen.append(dict(e.payload)),
            )
            app.close()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["path"], str(path))


if __name__ == "__main__":
    unittest.main()
