"""Integration tests for ProcessRunner.

These tests spawn real subprocesses using sys.executable so they work
on any platform that can run Python. No shell-specific commands are
used in ARGV mode tests.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest

from asat.execution import ExecutionMode, ExecutionRequest
from asat.runner import ProcessRunner


def _python(script: str, mode: ExecutionMode = ExecutionMode.ARGV) -> ExecutionRequest:
    """Build a request that runs the given inline Python script."""
    command = f'{sys.executable} -c "{script}"'
    return ExecutionRequest(command=command, mode=mode)


class RunnerStdoutStderrTests(unittest.TestCase):

    def setUp(self) -> None:
        self.runner = ProcessRunner()

    def test_captures_stdout(self) -> None:
        result = self.runner.run(_python("print('hello')"))
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse(result.timed_out)

    def test_captures_stderr_separately(self) -> None:
        script = "import sys; sys.stderr.write('oops\\n'); sys.exit(3)"
        result = self.runner.run(_python(script))
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn("oops", result.stderr)

    def test_preserves_both_streams(self) -> None:
        script = (
            "import sys; "
            "print('out'); "
            "sys.stderr.write('err\\n'); "
            "sys.exit(0)"
        )
        result = self.runner.run(_python(script))
        self.assertEqual(result.exit_code, 0)
        self.assertIn("out", result.stdout)
        self.assertIn("err", result.stderr)


class RunnerStreamingTests(unittest.TestCase):

    def test_stdout_handler_receives_lines(self) -> None:
        script = "import sys; print('a'); print('b'); print('c')"
        received: list[str] = []
        lock = threading.Lock()

        def handler(line: str) -> None:
            with lock:
                received.append(line)

        runner = ProcessRunner()
        runner.run(_python(script), stdout_handler=handler)
        self.assertEqual([line.strip() for line in received], ["a", "b", "c"])

    def test_stderr_handler_receives_lines(self) -> None:
        script = "import sys; sys.stderr.write('x\\ny\\n')"
        received: list[str] = []

        def handler(line: str) -> None:
            received.append(line)

        ProcessRunner().run(_python(script), stderr_handler=handler)
        self.assertEqual([line.strip() for line in received], ["x", "y"])


class RunnerTimeoutTests(unittest.TestCase):

    def test_timeout_kills_process(self) -> None:
        request = ExecutionRequest(
            command=f'{sys.executable} -c "import time; time.sleep(10)"',
            timeout_seconds=0.2,
        )
        result = ProcessRunner().run(request)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.exit_code, 0)


class RunnerCancelTests(unittest.TestCase):
    """F1: ProcessRunner.cancel() terminates the in-flight subprocess."""

    def test_cancel_with_no_active_process_returns_false(self) -> None:
        self.assertFalse(ProcessRunner().cancel())

    def test_cancel_terminates_running_subprocess(self) -> None:
        request = ExecutionRequest(
            command=f'{sys.executable} -c "import time; time.sleep(10)"',
        )
        runner = ProcessRunner()
        signalled = threading.Event()

        def cancel_after_start() -> None:
            # Wait until the run loop has stored the active process.
            for _ in range(200):  # up to ~2s
                if runner._active_process is not None:
                    break
                threading.Event().wait(0.01)
            self.assertTrue(runner.cancel())
            signalled.set()

        threading.Thread(target=cancel_after_start, daemon=True).start()
        result = runner.run(request)
        self.assertTrue(signalled.wait(timeout=5.0))
        # Cancelled processes exit non-zero (SIGTERM on POSIX, terminate
        # on Windows). The exact code is OS-dependent so we only assert
        # the run returned and the process is no longer "active".
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)
        self.assertIsNone(runner._active_process)


class RunnerErrorTests(unittest.TestCase):

    def test_missing_executable_raises(self) -> None:
        request = ExecutionRequest(command="definitely_not_a_real_command_xyz")
        with self.assertRaises(FileNotFoundError):
            ProcessRunner().run(request)

    def test_unparseable_command_raises(self) -> None:
        request = ExecutionRequest(command='echo "unclosed')
        with self.assertRaises(ValueError):
            ProcessRunner().run(request)

    def test_empty_command_raises(self) -> None:
        with self.assertRaises(ValueError):
            ProcessRunner().run(ExecutionRequest(command="   "))

    def test_empty_shell_command_raises(self) -> None:
        request = ExecutionRequest(command="", mode=ExecutionMode.SHELL)
        with self.assertRaises(ValueError):
            ProcessRunner().run(request)


class RunnerEnvironmentTests(unittest.TestCase):

    def test_env_is_passed_through(self) -> None:
        script = "import os; print(os.environ.get('ASAT_TEST_VAR', 'missing'))"
        request = ExecutionRequest(
            command=f'{sys.executable} -c "{script}"',
            env={**os.environ, "ASAT_TEST_VAR": "present"},
        )
        result = ProcessRunner().run(request)
        self.assertIn("present", result.stdout)


class RunnerShellModeTests(unittest.TestCase):

    def test_shell_mode_runs_a_simple_command(self) -> None:
        request = ExecutionRequest(
            command=f"{sys.executable} -c \"print('shelled')\"",
            mode=ExecutionMode.SHELL,
        )
        result = ProcessRunner().run(request)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("shelled", result.stdout)


if __name__ == "__main__":
    unittest.main()
