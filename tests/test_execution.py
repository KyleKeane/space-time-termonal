"""Unit tests for the execution value objects."""

from __future__ import annotations

import unittest
from pathlib import Path

from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult


class ExecutionRequestTests(unittest.TestCase):

    def test_defaults(self) -> None:
        request = ExecutionRequest(command="ls")
        self.assertEqual(request.command, "ls")
        self.assertEqual(request.mode, ExecutionMode.ARGV)
        self.assertIsNone(request.cwd)
        self.assertIsNone(request.env)
        self.assertIsNone(request.timeout_seconds)

    def test_custom_fields(self) -> None:
        request = ExecutionRequest(
            command="ls -la",
            mode=ExecutionMode.SHELL,
            cwd=Path("/tmp"),
            env={"KEY": "VALUE"},
            timeout_seconds=5.0,
        )
        self.assertEqual(request.mode, ExecutionMode.SHELL)
        self.assertEqual(request.cwd, Path("/tmp"))
        self.assertEqual(request.env, {"KEY": "VALUE"})
        self.assertEqual(request.timeout_seconds, 5.0)

    def test_is_frozen(self) -> None:
        request = ExecutionRequest(command="ls")
        with self.assertRaises(Exception):
            request.command = "rm -rf /"  # type: ignore[misc]


class ExecutionResultTests(unittest.TestCase):

    def test_defaults(self) -> None:
        result = ExecutionResult(stdout="hi\n", stderr="", exit_code=0)
        self.assertEqual(result.stdout, "hi\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)

    def test_timed_out_flag(self) -> None:
        result = ExecutionResult(stdout="", stderr="", exit_code=-9, timed_out=True)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, -9)


if __name__ == "__main__":
    unittest.main()
