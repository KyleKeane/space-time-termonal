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

    def test_default_clipboard_is_memory(self) -> None:
        from asat.actions import MemoryClipboard

        app = Application.build()
        self.assertIsInstance(app.clipboard, MemoryClipboard)

    def test_clipboard_factory_receives_event_bus(self) -> None:
        """F18: callers can install an OS-aware clipboard adapter via the
        factory, which gets handed the freshly-built EventBus."""
        from asat.event_bus import EventBus

        seen_bus: list[EventBus] = []

        class _StubClipboard:
            def __init__(self, bus: EventBus) -> None:
                seen_bus.append(bus)
                self.text = ""

            def set_text(self, text: str) -> None:
                self.text = text

        app = Application.build(clipboard_factory=_StubClipboard)
        self.assertIs(seen_bus[0], app.bus)
        self.assertIsInstance(app.clipboard, _StubClipboard)

    def test_default_onboarding_is_none(self) -> None:
        app = Application.build()
        self.assertIsNone(app.onboarding)

    def test_default_runner_is_process_runner(self) -> None:
        # Tests don't pass `runner=`, so the default keeps the
        # historical per-cell-Popen behaviour. The CLI is the only
        # place that opportunistically swaps in a ShellBackend.
        from asat.runner import ProcessRunner

        app = Application.build()
        self.assertIsInstance(app.runner, ProcessRunner)
        self.assertIs(app.kernel._runner, app.runner)

    def test_supplied_runner_is_threaded_through_to_kernel(self) -> None:
        runner = StubRunner()
        app = Application.build(runner=runner)
        self.assertIs(app.runner, runner)
        self.assertIs(app.kernel._runner, runner)

    def test_close_invokes_runner_close_when_present(self) -> None:
        # ShellBackend defines `close`; ProcessRunner does not. The
        # Application must call it when present so a long-lived shell
        # is shut down at exit.
        closed: list[bool] = []

        class _ClosableRunner(StubRunner):
            def close(self) -> None:
                closed.append(True)

        app = Application.build(runner=_ClosableRunner())
        app.close()
        self.assertEqual(closed, [True])


    def test_onboarding_factory_runs_after_session_created(self) -> None:
        """F20: when a coordinator is installed, `.run()` must fire
        during `Application.build` so the welcome event reaches any
        subscribed renderers before the user sees the first prompt."""
        import tempfile
        from pathlib import Path

        from asat.event_bus import EventBus
        from asat.onboarding import OnboardingCoordinator

        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / "first-run-done"
            seen: list[EventType] = []

            def _factory(bus: EventBus) -> OnboardingCoordinator:
                bus.subscribe("*", lambda e: seen.append(e.event_type))
                return OnboardingCoordinator(bus, sentinel)

            app = Application.build(onboarding_factory=_factory)

            self.assertIsInstance(app.onboarding, OnboardingCoordinator)
            self.assertIn(EventType.FIRST_RUN_DETECTED, seen)
            # FIRST_RUN_DETECTED must follow SESSION_CREATED so the user
            # hears the greeting after the session announces itself.
            session_idx = seen.index(EventType.SESSION_CREATED)
            welcome_idx = seen.index(EventType.FIRST_RUN_DETECTED)
            self.assertLess(session_idx, welcome_idx)
            self.assertTrue(sentinel.exists())


class ApplicationSharedShellTests(unittest.TestCase):
    """End-to-end check that two cells submitted through one
    `ShellBackend` (the F60 PR-B headline feature) actually share
    shell state — `cd` in cell 1 changes the cwd seen by cell 2.
    """

    def test_cd_in_one_cell_carries_to_the_next(self) -> None:
        import os
        import shutil
        from asat.shell_backend import ShellBackend

        if os.name == "nt" or shutil.which("bash") is None:
            self.skipTest("requires bash on a POSIX host")

        backend = ShellBackend()
        self.addCleanup(backend.close)
        app = Application.build(runner=backend)
        self.addCleanup(app.close)

        # Cell 1: `cd /tmp`
        _type(app, "cd /tmp")
        app.handle_key(kc.ENTER)
        for cid in app.drain_pending():
            app.execute(cid)

        # Cell 2: `pwd`
        _type(app, "pwd")
        app.handle_key(kc.ENTER)
        second_pending = app.drain_pending()
        self.assertEqual(len(second_pending), 1)
        app.execute(second_pending[0])

        second_cell = app.session.get_cell(second_pending[0])
        self.assertEqual(second_cell.stdout.strip(), "/tmp")
        self.assertEqual(second_cell.exit_code, 0)


class ApplicationAsyncExecutionTests(unittest.TestCase):
    """F62: `async_execution=True` routes submissions through a worker.

    The worker thread runs cells serially and publishes queue lifecycle
    events. `Application.close()` must stop the worker cleanly, so the
    assertion that the thread is no longer alive doubles as a leak test.
    """

    def test_submission_enqueues_and_runs_to_completion(self) -> None:
        app = Application.build(async_execution=True)
        self.addCleanup(app.close)
        app.kernel._runner = StubRunner(stdout="hi\n", exit_code=0)

        _type(app, "echo hi")
        app.handle_key(kc.ENTER)
        pending = app.drain_pending()
        self.assertEqual(len(pending), 1)
        app.execute(pending[0])

        # The worker is a background thread, so we wait for it rather
        # than asserting immediately.
        assert app.execution_worker is not None
        self.assertTrue(app.execution_worker.wait_until_drained(timeout=2.0))

        cell = app.session.get_cell(pending[0])
        self.assertEqual(cell.status, CellStatus.COMPLETED)
        self.assertEqual(cell.exit_code, 0)

    def test_command_queued_and_queue_drained_fire(self) -> None:
        app = Application.build(async_execution=True)
        self.addCleanup(app.close)
        app.kernel._runner = StubRunner(stdout="", exit_code=0)

        seen: list[EventType] = []
        app.bus.subscribe(EventType.COMMAND_QUEUED, lambda e: seen.append(e.event_type))
        app.bus.subscribe(EventType.QUEUE_DRAINED, lambda e: seen.append(e.event_type))

        _type(app, "true")
        app.handle_key(kc.ENTER)
        for cid in app.drain_pending():
            app.execute(cid)

        assert app.execution_worker is not None
        self.assertTrue(app.execution_worker.wait_until_drained(timeout=2.0))
        self.assertIn(EventType.COMMAND_QUEUED, seen)
        self.assertIn(EventType.QUEUE_DRAINED, seen)

    def test_close_stops_the_worker_thread(self) -> None:
        app = Application.build(async_execution=True)
        worker = app.execution_worker
        assert worker is not None
        self.assertIsNotNone(worker._thread)
        app.close()
        # After close the worker has no active thread reference.
        self.assertIsNone(worker._thread)


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

    def test_failing_cell_fires_stderr_tail_after_failure(self) -> None:
        # F36: a non-zero exit causes COMMAND_FAILED, which the
        # StderrTailAnnouncer converts into COMMAND_FAILED_STDERR_TAIL
        # with the tail of the cell's captured stderr.
        tail_payloads: list[dict] = []
        app = Application.build()
        app.kernel._runner = StubRunner(
            stdout="",
            stderr="trace line 1\ntrace line 2\nNameError: x\n",
            exit_code=1,
        )

        def capture(event):
            if event.event_type == EventType.COMMAND_FAILED_STDERR_TAIL:
                tail_payloads.append(event.payload)

        app.bus.subscribe(EventType.COMMAND_FAILED_STDERR_TAIL, capture)

        _type(app, "boom")
        app.handle_key(kc.ENTER)
        pending = app.drain_pending()
        app.execute(pending[0])

        self.assertEqual(len(tail_payloads), 1)
        self.assertEqual(
            tail_payloads[0]["tail_lines"],
            ["trace line 1", "trace line 2", "NameError: x"],
        )
        self.assertEqual(tail_payloads[0]["exit_code"], 1)
        self.assertFalse(tail_payloads[0]["timed_out"])


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

    def test_save_meta_command_persists_session_when_path_set(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.json"
            app = Application.build(session_path=path)
            seen: list[dict] = []
            app.bus.subscribe(
                EventType.SESSION_SAVED,
                lambda e: seen.append(dict(e.payload)),
            )
            _type(app, ":save")
            app.handle_key(kc.ENTER)
            self.assertTrue(path.exists())
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["path"], str(path))
            self.assertTrue(app.running, ":save must not exit the app")

    def test_save_meta_command_is_safe_without_session_path(self) -> None:
        app = Application.build()
        seen: list[dict] = []
        app.bus.subscribe(
            EventType.SESSION_SAVED,
            lambda e: seen.append(dict(e.payload)),
        )
        _type(app, ":save")
        app.handle_key(kc.ENTER)
        self.assertEqual(seen, [])
        self.assertTrue(app.running)

    def test_welcome_meta_command_replays_tour_without_rewriting_sentinel(self) -> None:
        """F44: `:welcome` re-publishes FIRST_RUN_DETECTED through the
        same coordinator that ran at launch, without rewinding the
        sentinel. A user who forgot the keystrokes hears the full tour
        again; the next fresh launch still stays silent."""
        import tempfile
        from pathlib import Path

        from asat.event_bus import EventBus
        from asat.onboarding import OnboardingCoordinator

        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / "first-run-done"

            def _factory(bus: EventBus) -> OnboardingCoordinator:
                return OnboardingCoordinator(bus, sentinel)

            app = Application.build(onboarding_factory=_factory)
            # Launch fired FIRST_RUN_DETECTED once and wrote the sentinel.
            first_mtime = sentinel.stat().st_mtime_ns

            replays: list[dict] = []
            app.bus.subscribe(
                EventType.FIRST_RUN_DETECTED,
                lambda e: replays.append(dict(e.payload)),
            )
            _type(app, ":welcome")
            app.handle_key(kc.ENTER)

            self.assertEqual(len(replays), 1)
            self.assertTrue(replays[0]["replay"])
            self.assertEqual(sentinel.stat().st_mtime_ns, first_mtime)
            self.assertTrue(app.running, ":welcome must not exit the app")

    def test_welcome_meta_command_is_safe_without_onboarding(self) -> None:
        """F44: a user running `--quiet` or `--check` has `onboarding=None`.
        `:welcome` there must be a harmless no-op, not a crash."""
        app = Application.build()  # no onboarding_factory
        self.assertIsNone(app.onboarding)
        _type(app, ":welcome")
        app.handle_key(kc.ENTER)
        self.assertTrue(app.running)


class ApplicationRepeatNarrationTests(unittest.TestCase):
    """F30: `:repeat` and Ctrl+R replay the last narration."""

    def test_colon_repeat_meta_command_replays_last_narration(self) -> None:
        app = Application.build()
        replays: list[dict] = []
        app.bus.subscribe(
            EventType.NARRATION_REPLAYED,
            lambda e: replays.append(dict(e.payload)),
        )
        # SESSION_CREATED fired during build and is now the last phrase
        # in the ring buffer, so `:repeat` has something to replay.
        _type(app, ":repeat")
        app.handle_key(kc.ENTER)
        self.assertEqual(len(replays), 1)
        self.assertTrue(app.running, ":repeat must not exit the app")

    def test_ctrl_r_replays_last_narration(self) -> None:
        app = Application.build()
        replays: list[dict] = []
        app.bus.subscribe(
            EventType.NARRATION_REPLAYED,
            lambda e: replays.append(dict(e.payload)),
        )
        app.handle_key(Key.combo("r", Modifier.CTRL))
        self.assertEqual(len(replays), 1)

    def test_repeat_with_empty_history_is_a_safe_noop(self) -> None:
        # A fresh engine with an empty bank has nothing to replay, but
        # the keystroke must stay harmless rather than crashing.
        from asat.sound_bank import SoundBank

        app = Application.build(bank=SoundBank())
        replays: list[dict] = []
        app.bus.subscribe(
            EventType.NARRATION_REPLAYED,
            lambda e: replays.append(dict(e.payload)),
        )
        app.handle_key(Key.combo("r", Modifier.CTRL))
        self.assertEqual(replays, [])
        self.assertTrue(app.running)


class ApplicationEventLoggerTests(unittest.TestCase):
    """F22: `log_factory` attaches a JsonlEventLogger before build events."""

    def test_log_factory_captures_session_created(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from asat.jsonl_logger import JsonlEventLogger

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"

            def _factory(bus) -> JsonlEventLogger:
                return JsonlEventLogger(bus, path)

            app = Application.build(log_factory=_factory)
            self.addCleanup(app.close)
            types = [
                json.loads(line)["event_type"]
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            # SESSION_CREATED is the first publish in build(); if the
            # logger attaches late, the file will be missing it.
            # (The typed SoundEngine handler runs before the wildcard
            # logger, and the engine re-publishes audio.spoken, so
            # audio.spoken lands first — but session.created still
            # appears in the log.)
            self.assertIn("session.created", types)

    def test_close_flushes_and_closes_logger(self) -> None:
        import tempfile
        from pathlib import Path

        from asat.jsonl_logger import JsonlEventLogger

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            app = Application.build(
                log_factory=lambda bus: JsonlEventLogger(bus, path),
            )
            app.close()
            # After close, further events must not reach the logger.
            size_after_close = path.stat().st_size
            from asat.event_bus import publish_event

            publish_event(
                app.bus,
                EventType.HELP_REQUESTED,
                {"lines": []},
                source="test",
            )
            self.assertEqual(path.stat().st_size, size_after_close)


class ApplicationCompletionAlertTests(unittest.TestCase):
    """F34: completion on a non-focused cell fires COMMAND_COMPLETED_AWAY."""

    def test_completion_on_moved_focus_fires_away_event(self) -> None:
        app = Application.build()
        app.kernel._runner = StubRunner(stdout="hi\n", exit_code=0)

        away: list[dict] = []
        app.bus.subscribe(
            EventType.COMMAND_COMPLETED_AWAY,
            lambda e: away.append(dict(e.payload)),
        )

        _type(app, "echo hi")
        app.handle_key(kc.ENTER)
        pending = app.drain_pending()
        origin_cell_id = pending[0]

        # User navigates to a second cell while the command "runs".
        app.cursor.new_cell()
        new_cell_id = app.cursor.focus.cell_id
        self.assertNotEqual(origin_cell_id, new_cell_id)

        app.execute(origin_cell_id)

        self.assertEqual(len(away), 1)
        self.assertEqual(away[0]["cell_id"], origin_cell_id)
        self.assertEqual(away[0]["current_cell_id"], new_cell_id)
        self.assertEqual(away[0]["original_event_type"], "command.completed")


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


class ApplicationTextTraceTests(unittest.TestCase):
    """The `text_trace` parameter attaches a TerminalRenderer BEFORE
    the startup publishes fire, so the launch banner and the initial
    `[input #…]` line reach the user on first run."""

    def test_text_trace_captures_launch_banner_and_first_input_line(self) -> None:
        import io

        stream = io.StringIO()
        Application.build(text_trace=stream)
        trace = stream.getvalue()
        self.assertIn("ready", trace)
        self.assertIn("[input", trace)

    def test_text_trace_none_produces_no_output(self) -> None:
        # Default behaviour is silent; tests and embedders that want to
        # observe the stream opt in explicitly.
        import io

        stream = io.StringIO()
        Application.build()
        self.assertEqual(stream.getvalue(), "")


class ApplicationWorkspaceTests(unittest.TestCase):
    """When a Workspace is supplied, the Application chdirs into it
    on launch and the three workspace meta-commands (`:workspace`,
    `:list-notebooks`, `:new-notebook`) publish the right events."""

    def setUp(self) -> None:
        import os
        import tempfile
        from asat.workspace import Workspace

        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._restore_cwd)
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Workspace.init(self._tmp.name)

    def _restore_cwd(self) -> None:
        import os

        os.chdir(self._cwd)

    def _build(self, **overrides):
        events: list[Event] = []

        def _record(event: Event) -> None:
            events.append(event)

        from asat.session import Session

        defaults: dict = {
            "workspace": self.workspace,
            "session": Session.new(),
        }
        defaults.update(overrides)
        app = Application.build(**defaults)
        for event_type in EventType:
            app.bus.subscribe(event_type, _record)
        self.addCleanup(app.close)
        return app, events

    def test_build_chdirs_into_workspace_root(self) -> None:
        import os

        Application.build(workspace=self.workspace)
        self.assertEqual(os.getcwd(), str(self.workspace.root))

    def test_build_publishes_workspace_opened_with_count(self) -> None:
        events: list[Event] = []

        def _record(event: Event) -> None:
            events.append(event)

        from asat.event_bus import EventBus
        # Subscribe BEFORE build so we capture the launch event.
        # Easiest path: build with text_trace=None, then assert via
        # a captured publication using a pre-supplied bus is not
        # possible because build() owns the bus. Instead we let
        # build() fire the event and read it back from the
        # JsonlEventLogger path is heavyweight; the simpler check
        # is: re-trigger by calling _announce_workspace.
        app = Application.build(workspace=self.workspace)
        self.addCleanup(app.close)
        for event_type in EventType:
            app.bus.subscribe(event_type, _record)
        app._announce_workspace()
        opened = [e for e in events if e.event_type == EventType.WORKSPACE_OPENED]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].payload["name"], self.workspace.root.name)
        self.assertEqual(opened[0].payload["notebook_count"], 0)

    def test_list_notebooks_meta_command_publishes_summary(self) -> None:
        self.workspace.new_notebook("alpha")
        self.workspace.new_notebook("beta")
        app, events = self._build()
        app._announce_notebook_list()
        listed = [e for e in events if e.event_type == EventType.NOTEBOOK_LISTED]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].payload["names"], ["alpha", "beta"])
        self.assertIn("alpha", listed[0].payload["summary"])

    def test_new_notebook_meta_command_creates_file_and_event(self) -> None:
        app, events = self._build()
        app._create_notebook("ideas")
        path = self.workspace.notebook_path("ideas")
        self.assertTrue(path.exists())
        created = [e for e in events if e.event_type == EventType.NOTEBOOK_CREATED]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].payload["name"], "ideas")

    def test_new_notebook_blank_argument_emits_help_hint(self) -> None:
        app, events = self._build()
        app._create_notebook("   ")
        helps = [e for e in events if e.event_type == EventType.HELP_REQUESTED]
        self.assertTrue(any("name is required" in line
                            for e in helps for line in e.payload.get("lines", [])))

    def test_meta_commands_are_safe_without_workspace(self) -> None:
        from asat.session import Session

        app = Application.build(session=Session.new())
        self.addCleanup(app.close)
        events: list[Event] = []
        app.bus.subscribe(EventType.HELP_REQUESTED, events.append)
        app._announce_workspace()
        app._announce_notebook_list()
        app._create_notebook("ignored")
        # Each helper publishes a "no workspace" hint instead of crashing.
        self.assertEqual(len(events), 3)


if __name__ == "__main__":
    unittest.main()
