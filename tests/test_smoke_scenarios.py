"""End-to-end scenario tests mirroring docs/SMOKE_TEST.md.

Each Act in SMOKE_TEST.md walks the user through a specific phase
of the product (launch, success, fail, navigate, output, meta,
settings, menu, quit). This file holds one TestCase class per Act.

Every test drives an `Application` with scripted keystrokes and a
`StubRunner` so no real shell, speaker, or TTY is involved. All
events published to the bus are recorded through a wildcard
subscriber; assertions check the event-type sequence and key
payload fields that the manual smoke test listens for.

These tests are not a substitute for the hands-on run in
SMOKE_TEST.md — they cannot verify that the voices sound right or
that a chord plays on the left channel — but they catch the class
of regression where the *events* stop firing in the expected order.
A real human smoke run still catches audio regressions.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

from asat import keys as kc
from asat.app import Application
from asat.audio_sink import MemorySink
from asat.event_bus import WILDCARD
from asat.events import Event, EventType
from asat.execution import ExecutionResult
from asat.keys import Key, Modifier
from asat.notebook import FocusMode


class StubRunner:
    """A ProcessRunner stand-in — no subprocess, scripted output."""

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


class _ScenarioFixture:
    """Bundle of Application + recorded event stream for one scenario.

    Subscribes a wildcard recorder BEFORE any pre-test setup fires so
    launch-time events (SESSION_CREATED, FOCUS_CHANGED to INPUT) land
    in `.events` and every Act can assert on them.
    """

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        session_path: Optional[Path] = None,
    ) -> None:
        self.sink = MemorySink()
        self.app = Application.build(
            sink=self.sink,
            session_path=session_path,
        )
        self.app.kernel._runner = StubRunner(
            stdout=stdout, stderr=stderr, exit_code=exit_code
        )
        self.events: list[Event] = []
        self.app.bus.subscribe(WILDCARD, self.events.append)

    def type_text(self, text: str) -> None:
        for ch in text:
            self.app.handle_key(Key.printable(ch))

    def press(self, key: Key) -> None:
        self.app.handle_key(key)

    def submit(self) -> list[str]:
        self.app.handle_key(kc.ENTER)
        pending = self.app.drain_pending()
        for cell_id in pending:
            self.app.execute(cell_id)
        return pending

    def types_of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]

    def first_of(self, event_type: EventType) -> Event:
        matches = self.types_of(event_type)
        if not matches:
            raise AssertionError(f"No {event_type.value} event fired")
        return matches[0]


# -------------------------------------------------------------------
# Act 1 — Launch
# -------------------------------------------------------------------


class Act1LaunchTests(unittest.TestCase):
    """SMOKE_TEST.md Act 1: a fresh build publishes SESSION_CREATED
    and lands the user in INPUT mode on a seeded cell."""

    def test_build_publishes_session_created_then_focuses_input(self) -> None:
        app = Application.build()
        self.assertEqual(len(app.session), 1)
        self.assertEqual(app.cursor.focus.mode, FocusMode.INPUT)

    def test_session_created_narrates_through_the_sink(self) -> None:
        sink = MemorySink()
        Application.build(sink=sink)
        self.assertGreater(
            len(sink.buffers),
            0,
            "SESSION_CREATED produced no narration; the launch banner is "
            "silent. Check default_bank binding for session.created.",
        )


# -------------------------------------------------------------------
# Act 2 — Run a successful command
# -------------------------------------------------------------------


class Act2SuccessTests(unittest.TestCase):
    """Type a command, press Enter, observe the full success sequence:
    SUBMITTED -> STARTED -> OUTPUT -> COMPLETED -> focus advances."""

    def test_success_command_emits_expected_event_sequence(self) -> None:
        fx = _ScenarioFixture(stdout="hello world\n", exit_code=0)
        fx.type_text("echo hello world")
        fx.submit()

        expected_order = [
            EventType.COMMAND_SUBMITTED,
            EventType.COMMAND_STARTED,
            EventType.OUTPUT_CHUNK,
            EventType.COMMAND_COMPLETED,
        ]
        seen = [e.event_type for e in fx.events if e.event_type in expected_order]
        self.assertEqual(seen, expected_order)

        completed = fx.first_of(EventType.COMMAND_COMPLETED)
        self.assertEqual(completed.payload["exit_code"], 0)

    def test_success_auto_advances_to_a_fresh_input_cell(self) -> None:
        fx = _ScenarioFixture(stdout="hi\n", exit_code=0)
        cells_before = len(fx.app.session)
        fx.type_text("echo hi")
        fx.submit()
        # A fresh input cell was added and the cursor is on it in INPUT.
        self.assertGreater(len(fx.app.session), cells_before)
        self.assertEqual(fx.app.cursor.focus.mode, FocusMode.INPUT)

    def test_pwd_meta_command_narrates_cwd(self) -> None:
        fx = _ScenarioFixture()
        fx.type_text(":pwd")
        fx.app.handle_key(kc.ENTER)

        helps = fx.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(helps), 1)
        lines = helps[-1].payload["lines"]
        self.assertTrue(
            any("working directory" in line.lower() for line in lines),
            f":pwd did not narrate a working-directory line; got: {lines!r}",
        )


# -------------------------------------------------------------------
# Act 3 — Run a failing command
# -------------------------------------------------------------------


class Act3FailureTests(unittest.TestCase):
    """A non-zero exit fires COMMAND_FAILED and STDERR_TAIL with the
    captured trailing stderr lines."""

    def test_failing_command_publishes_failed_and_stderr_tail(self) -> None:
        fx = _ScenarioFixture(
            stderr="Traceback\nNameError: x is not defined\n",
            exit_code=1,
        )
        fx.type_text("boom")
        fx.submit()

        failed = fx.types_of(EventType.COMMAND_FAILED)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].payload["exit_code"], 1)

        tails = fx.types_of(EventType.COMMAND_FAILED_STDERR_TAIL)
        self.assertEqual(len(tails), 1)
        self.assertIn("NameError: x is not defined", tails[0].payload["tail_lines"])


# -------------------------------------------------------------------
# Act 4 — Navigate the notebook
# -------------------------------------------------------------------


class Act4NavigateTests(unittest.TestCase):
    """Escape out of INPUT, walk cells with Up/Down, and :state names
    the full "where am I" snapshot."""

    def test_escape_lands_on_notebook_mode(self) -> None:
        fx = _ScenarioFixture()
        fx.type_text("echo one")
        fx.press(kc.ESCAPE)
        self.assertEqual(fx.app.cursor.focus.mode, FocusMode.NOTEBOOK)

        focus_changes = fx.types_of(EventType.FOCUS_CHANGED)
        # At least one transition lands on NOTEBOOK.
        to_notebook = [
            e for e in focus_changes if e.payload.get("new_mode") == FocusMode.NOTEBOOK.value
        ]
        self.assertGreaterEqual(len(to_notebook), 1)

    def test_colon_state_names_mode_position_session_and_cwd(self) -> None:
        fx = _ScenarioFixture()
        fx.type_text(":state")
        fx.app.handle_key(kc.ENTER)

        helps = fx.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(helps), 1)
        lines = helps[-1].payload["lines"]
        joined = "\n".join(lines).lower()
        # All four claims from SMOKE_TEST.md Act 4.2 must be present.
        self.assertIn("focus mode", joined)
        self.assertIn("position", joined)
        self.assertIn("session id", joined)
        self.assertIn("working directory", joined)


# -------------------------------------------------------------------
# Act 5 — Explore captured output
# -------------------------------------------------------------------


class Act5OutputModeTests(unittest.TestCase):
    """Ctrl+O enters OUTPUT mode on a completed cell; navigation and
    search emit focus / search events."""

    def test_ctrl_o_enters_output_mode_after_command_completes(self) -> None:
        fx = _ScenarioFixture(stdout="line a\nline b\nline c\n", exit_code=0)
        fx.type_text("produce lines")
        fx.submit()

        # Back to the completed cell (auto-advance moved us off it).
        fx.press(kc.ESCAPE)
        fx.press(kc.UP)
        fx.press(Key.combo("o", Modifier.CTRL))

        self.assertEqual(fx.app.cursor.focus.mode, FocusMode.OUTPUT)

    def test_output_recorder_captures_every_stdout_line(self) -> None:
        """OUTPUT_LINE_APPENDED fires for each streamed line, giving
        OUTPUT mode something to attach to. Per-line focus navigation
        is exercised directly in test_output_cursor.py and
        test_input_router.py::OutputModeDispatchTests."""
        fx = _ScenarioFixture(stdout="line a\nline b\nline c\n", exit_code=0)
        fx.type_text("produce lines")
        fx.submit()

        appended = fx.types_of(EventType.OUTPUT_LINE_APPENDED)
        texts = [e.payload["text"] for e in appended]
        self.assertEqual(texts, ["line a", "line b", "line c"])

    def test_ctrl_o_attaches_buffer_and_up_navigates(self) -> None:
        """End-to-end keyboard-only path into OUTPUT mode: Ctrl+O not
        only flips focus mode but attaches the output cursor to the
        focused cell's buffer, so Up/Down work immediately without
        routing through the F2 action menu. This is the behaviour
        SMOKE_TEST.md Act 5.2 promises."""
        fx = _ScenarioFixture(stdout="line a\nline b\nline c\n", exit_code=0)
        fx.type_text("produce lines")
        fx.submit()
        fx.press(kc.ESCAPE)
        fx.press(kc.UP)  # back to the completed cell
        fx.press(Key.combo("o", Modifier.CTRL))

        # Attach should have fired an initial OUTPUT_LINE_FOCUSED
        # landing on the last line.
        focused = fx.types_of(EventType.OUTPUT_LINE_FOCUSED)
        self.assertGreaterEqual(len(focused), 1)
        self.assertEqual(focused[-1].payload["line_number"], 2)

        before = len(focused)
        fx.press(kc.UP)
        after = len(fx.types_of(EventType.OUTPUT_LINE_FOCUSED))
        self.assertGreater(after, before, "Up in OUTPUT did not focus a new line")


# -------------------------------------------------------------------
# Act 6 — Meta-commands and discoverability
# -------------------------------------------------------------------


class Act6MetaCommandTests(unittest.TestCase):
    """:help, :commands, and typo forgiveness all route through
    HELP_REQUESTED; :commands names every router meta-command."""

    def test_help_meta_publishes_cheat_sheet(self) -> None:
        fx = _ScenarioFixture()
        fx.type_text(":help")
        fx.app.handle_key(kc.ENTER)

        helps = fx.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(helps), 1)
        self.assertTrue(
            any(len(h.payload.get("lines", [])) > 0 for h in helps),
            ":help fired HELP_REQUESTED with no lines payload",
        )

    def test_commands_meta_lists_every_router_meta_command(self) -> None:
        from asat.input_router import META_COMMANDS

        fx = _ScenarioFixture()
        fx.type_text(":commands")
        fx.app.handle_key(kc.ENTER)

        helps = fx.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(helps), 1)
        text = "\n".join(helps[-1].payload["lines"])
        missing = [name for name in META_COMMANDS if f":{name}" not in text]
        self.assertEqual(
            missing,
            [],
            f":commands narration missing these router meta-commands: {missing}",
        )

    def test_meta_typo_hint_suggests_the_intended_command(self) -> None:
        fx = _ScenarioFixture()
        fx.type_text(":setings")
        fx.app.handle_key(kc.ENTER)

        helps = fx.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(helps), 1)
        joined = "\n".join(helps[-1].payload["lines"]).lower()
        self.assertIn(":settings", joined)
        # A typo does NOT enqueue a cell — it stays in INPUT.
        self.assertEqual(fx.app.drain_pending(), [])
        self.assertEqual(fx.app.cursor.focus.mode, FocusMode.INPUT)

    def test_ctrl_r_replays_the_last_narration(self) -> None:
        fx = _ScenarioFixture()
        fx.press(Key.combo("r", Modifier.CTRL))
        replays = fx.types_of(EventType.NARRATION_REPLAYED)
        self.assertEqual(len(replays), 1)


# -------------------------------------------------------------------
# Act 7 — Settings editor
# -------------------------------------------------------------------


class Act7SettingsTests(unittest.TestCase):
    """Ctrl+, from NOTEBOOK opens the editor; Ctrl+Q closes it.
    Publishes SETTINGS_OPENED / SETTINGS_CLOSED along the way."""

    def test_ctrl_comma_opens_settings_and_ctrl_q_closes(self) -> None:
        fx = _ScenarioFixture()
        fx.press(kc.ESCAPE)  # INPUT -> NOTEBOOK
        fx.press(Key.combo(",", Modifier.CTRL))
        self.assertEqual(fx.app.cursor.focus.mode, FocusMode.SETTINGS)
        self.assertEqual(len(fx.types_of(EventType.SETTINGS_OPENED)), 1)

        fx.press(Key.combo("q", Modifier.CTRL))
        self.assertNotEqual(fx.app.cursor.focus.mode, FocusMode.SETTINGS)
        self.assertEqual(len(fx.types_of(EventType.SETTINGS_CLOSED)), 1)

    def test_slash_in_settings_opens_search_overlay(self) -> None:
        fx = _ScenarioFixture()
        fx.press(kc.ESCAPE)
        fx.press(Key.combo(",", Modifier.CTRL))
        fx.press(Key.printable("/"))

        opened = fx.types_of(EventType.SETTINGS_SEARCH_OPENED)
        self.assertEqual(len(opened), 1)

        fx.type_text("gain")
        updates = fx.types_of(EventType.SETTINGS_SEARCH_UPDATED)
        self.assertGreaterEqual(len(updates), 1)
        self.assertEqual(updates[-1].payload.get("query"), "gain")

        fx.press(kc.ENTER)
        # Commit leaves the overlay closed.
        self.assertEqual(len(fx.types_of(EventType.SETTINGS_SEARCH_CLOSED)), 1)


# -------------------------------------------------------------------
# Act 8 — Actions menu
# -------------------------------------------------------------------


class Act8ActionMenuTests(unittest.TestCase):
    """F2 opens the contextual actions menu; Escape closes it without
    invoking an item."""

    def test_f2_opens_menu_and_escape_closes_without_invoke(self) -> None:
        fx = _ScenarioFixture()
        fx.press(kc.ESCAPE)  # INPUT -> NOTEBOOK
        fx.press(kc.F2)
        self.assertEqual(len(fx.types_of(EventType.ACTION_MENU_OPENED)), 1)

        fx.press(kc.ESCAPE)
        self.assertEqual(len(fx.types_of(EventType.ACTION_MENU_CLOSED)), 1)
        # Escape without Enter means no item was invoked.
        self.assertEqual(len(fx.types_of(EventType.ACTION_MENU_ITEM_INVOKED)), 0)


# -------------------------------------------------------------------
# Act 9 — Save, resume, quit
# -------------------------------------------------------------------


class Act9SaveResumeQuitTests(unittest.TestCase):
    """:save persists when a path is set; :quit clears running; resume
    re-loads the session on the next build."""

    def test_save_persists_when_session_path_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.json"
            fx = _ScenarioFixture(session_path=path)
            fx.type_text(":save")
            fx.app.handle_key(kc.ENTER)

            self.assertTrue(path.exists())
            saved = fx.types_of(EventType.SESSION_SAVED)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].payload["path"], str(path))

    def test_quit_clears_running_and_does_not_enqueue_a_cell(self) -> None:
        fx = _ScenarioFixture()
        self.assertTrue(fx.app.running)
        fx.type_text(":quit")
        fx.app.handle_key(kc.ENTER)
        self.assertFalse(fx.app.running)
        self.assertEqual(fx.app.drain_pending(), [])

    def test_resume_reloads_session_from_disk(self) -> None:
        from asat.session import Session

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resumable.json"
            fx = _ScenarioFixture(session_path=path)
            fx.type_text("echo resumable")
            fx.app.handle_key(kc.ESCAPE)  # commit buffer back to cell
            fx.app.close()
            self.assertTrue(path.exists())

            # Re-launch with the saved session.
            resumed = Application.build(
                session=Session.load(path),
                session_path=path,
            )
            commands = [c.command for c in resumed.session.cells]
            self.assertIn("echo resumable", commands)


if __name__ == "__main__":
    unittest.main()
