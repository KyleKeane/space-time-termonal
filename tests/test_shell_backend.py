"""Tests for ShellBackend — the persistent-shell runner for F60.

These tests exercise the real `bash` on the host. Skipped on Windows
or when bash is missing so the suite stays green in those environments.
The ProcessRunner-equivalent contract (line streaming, exit codes,
empty-command rejection) is verified, plus the F60-specific
guarantees: state carries between calls (cd, export, function
definition), sentinels never leak to the caller, malformed shell
output surfaces as `ShellBackendError`, and a timed-out command
interrupts without killing the shell.
"""

from __future__ import annotations

import os
import shutil
import unittest

from asat.execution import ExecutionRequest
from asat.shell_backend import (
    ShellBackend,
    ShellBackendError,
    shell_backend_or_none,
)


_BASH = shutil.which("bash")
_REQUIRES_BASH = unittest.skipUnless(
    _BASH is not None and os.name != "nt",
    "ShellBackend tests require bash on a POSIX host",
)


@_REQUIRES_BASH
class ShellBackendBasicTests(unittest.TestCase):
    """Round-trip: a single command, line streaming, exit codes."""

    def setUp(self) -> None:
        self.backend = ShellBackend()
        self.addCleanup(self.backend.close)

    def test_echo_returns_stdout_with_zero_exit(self) -> None:
        result = self.backend.run(ExecutionRequest(command="echo hello"))
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)

    def test_nonzero_exit_propagates(self) -> None:
        result = self.backend.run(ExecutionRequest(command="false"))
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.timed_out)

    def test_stderr_separated_from_stdout(self) -> None:
        result = self.backend.run(
            ExecutionRequest(command="echo out; echo err >&2")
        )
        self.assertEqual(result.stdout.strip(), "out")
        self.assertEqual(result.stderr.strip(), "err")

    def test_line_handlers_receive_each_line(self) -> None:
        out_lines: list[str] = []
        err_lines: list[str] = []
        self.backend.run(
            ExecutionRequest(command="printf 'a\\nb\\nc\\n'; printf 'x\\n' >&2"),
            stdout_handler=out_lines.append,
            stderr_handler=err_lines.append,
        )
        self.assertEqual([line.rstrip("\n") for line in out_lines], ["a", "b", "c"])
        self.assertEqual([line.rstrip("\n") for line in err_lines], ["x"])

    def test_empty_command_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.backend.run(ExecutionRequest(command="   "))

    def test_sentinel_prefix_in_user_output_is_not_truncated(self) -> None:
        # A user-supplied line that *contains* the sentinel prefix
        # mid-line must still flow through verbatim — only lines that
        # *start* with the prefix are sentinel framing.
        leak_attempt = "echo 'leading __ASAT_SHELL_END_xxx__:0'"
        result = self.backend.run(ExecutionRequest(command=leak_attempt))
        self.assertIn("leading __ASAT_SHELL_END_xxx__:0", result.stdout)
        self.assertEqual(result.exit_code, 0)


@_REQUIRES_BASH
class ShellBackendStatePersistenceTests(unittest.TestCase):
    """The headline F60 guarantee: state carries between cells."""

    def setUp(self) -> None:
        self.backend = ShellBackend()
        self.addCleanup(self.backend.close)

    def test_cd_persists_across_commands(self) -> None:
        self.backend.run(ExecutionRequest(command="cd /tmp"))
        result = self.backend.run(ExecutionRequest(command="pwd"))
        self.assertEqual(result.stdout.strip(), "/tmp")

    def test_export_persists_across_commands(self) -> None:
        self.backend.run(ExecutionRequest(command="export ASAT_F60_TEST=hello"))
        result = self.backend.run(ExecutionRequest(command="echo $ASAT_F60_TEST"))
        self.assertEqual(result.stdout.strip(), "hello")

    def test_function_definition_persists_across_commands(self) -> None:
        self.backend.run(
            ExecutionRequest(command="greet() { echo hi-from-func; }")
        )
        result = self.backend.run(ExecutionRequest(command="greet"))
        self.assertEqual(result.stdout.strip(), "hi-from-func")

    def test_shell_option_persists_across_commands(self) -> None:
        # `set -o pipefail` is the safest cross-call shell-option test:
        # `set -u` and `set -e` cause non-interactive bash to exit on
        # the *next* offending command, which would tear down the
        # backend itself. `pipefail` only changes the pipeline exit
        # code, leaving the shell alive.
        self.backend.run(ExecutionRequest(command="set -o pipefail"))
        result = self.backend.run(ExecutionRequest(command="false | true"))
        # Without pipefail this is 0; with pipefail it propagates the
        # `false` from earlier in the pipeline.
        self.assertNotEqual(result.exit_code, 0)


@_REQUIRES_BASH
class ShellBackendTimeoutTests(unittest.TestCase):
    """A timeout interrupts the command but keeps the shell alive."""

    def setUp(self) -> None:
        self.backend = ShellBackend()
        self.addCleanup(self.backend.close)

    def test_timeout_marks_result_and_keeps_shell_running(self) -> None:
        result = self.backend.run(
            ExecutionRequest(command="sleep 5", timeout_seconds=0.3)
        )
        self.assertTrue(result.timed_out)
        self.assertTrue(self.backend.is_alive())
        # The shell survived; the next command runs normally.
        followup = self.backend.run(ExecutionRequest(command="echo after"))
        self.assertEqual(followup.stdout.strip(), "after")
        self.assertEqual(followup.exit_code, 0)


@_REQUIRES_BASH
class ShellBackendCancelTests(unittest.TestCase):
    """F1: cancel() interrupts the running command without killing the shell."""

    def setUp(self) -> None:
        self.backend = ShellBackend()
        self.addCleanup(self.backend.close)

    def test_cancel_when_idle_returns_true_or_false_safely(self) -> None:
        # The shell is alive but no command is running. `cancel`
        # currently signals the process group regardless; we just
        # require the shell stays alive afterwards so a stray cancel
        # doesn't tear the session down.
        self.backend.cancel()
        self.assertTrue(self.backend.is_alive())

    def test_cancel_after_close_returns_false(self) -> None:
        backend = ShellBackend()
        backend.close()
        self.assertFalse(backend.cancel())

    def test_cancel_interrupts_running_command_without_killing_shell(self) -> None:
        import threading
        signal_sent = threading.Event()

        def cancel_after_start() -> None:
            # Give the shell a moment to start the sleep, then signal.
            threading.Event().wait(0.2)
            self.backend.cancel()
            signal_sent.set()

        threading.Thread(target=cancel_after_start, daemon=True).start()
        result = self.backend.run(ExecutionRequest(command="sleep 5"))
        self.assertTrue(signal_sent.wait(timeout=5.0))
        # SIGINT-killed bash command exits 130; the shell itself stays
        # up because of the `trap : INT` handler set on construction.
        self.assertEqual(result.exit_code, 130)
        self.assertTrue(self.backend.is_alive())
        followup = self.backend.run(ExecutionRequest(command="echo after"))
        self.assertEqual(followup.stdout.strip(), "after")
        self.assertEqual(followup.exit_code, 0)


@_REQUIRES_BASH
class ShellBackendLifecycleTests(unittest.TestCase):
    """Start-up, shutdown, and crash detection."""

    def test_close_terminates_the_shell(self) -> None:
        backend = ShellBackend()
        self.assertTrue(backend.is_alive())
        backend.close()
        self.assertFalse(backend.is_alive())

    def test_run_after_close_raises(self) -> None:
        backend = ShellBackend()
        backend.close()
        with self.assertRaises(ShellBackendError):
            backend.run(ExecutionRequest(command="echo nope"))

    def test_unknown_shell_raises(self) -> None:
        with self.assertRaises(ShellBackendError):
            ShellBackend(shell=("definitely-not-a-shell-12345",))

    def test_explicit_exit_inside_command_marks_shell_dead(self) -> None:
        backend = ShellBackend()
        self.addCleanup(backend.close)
        # Asking the shell itself to exit closes both pipes mid-command.
        # The collector detects EOF on stdout before the sentinel and
        # raises ShellBackendError.
        with self.assertRaises(ShellBackendError):
            backend.run(ExecutionRequest(command="exit 7"))
        self.assertFalse(backend.is_alive())


class ShellBackendOptionalConstructorTests(unittest.TestCase):
    """`shell_backend_or_none` returns None instead of raising."""

    def test_returns_none_on_windows(self) -> None:
        # Smoke check: on POSIX with bash present we get an instance,
        # on Windows we get None. We only need to assert the
        # documented contract holds for whichever platform we're on.
        backend = shell_backend_or_none()
        if os.name == "nt":
            self.assertIsNone(backend)
        else:
            if _BASH is None:
                self.assertIsNone(backend)
            else:
                self.assertIsNotNone(backend)
                assert backend is not None  # appease the type checker
                backend.close()

    def test_returns_none_on_missing_shell(self) -> None:
        result = shell_backend_or_none(shell=("definitely-not-a-shell-12345",))
        self.assertIsNone(result)
