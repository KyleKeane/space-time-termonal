"""Unit tests for ExecutionKernel.

The kernel is tested with a stub ProcessRunner for most cases to keep
the tests fast and deterministic. One end-to-end test exercises a real
subprocess to confirm the wiring from kernel through runner to cell.
"""

from __future__ import annotations

import sys
import unittest
from typing import Optional

from asat.cell import Cell, CellStatus
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.kernel import EXIT_CODE_NOT_FOUND, EXIT_CODE_PARSE_ERROR, ExecutionKernel
from asat.runner import ProcessRunner


class StubRunner:
    """ProcessRunner stand-in for fast unit tests."""

    def __init__(
        self,
        result: Optional[ExecutionResult] = None,
        raises: Optional[BaseException] = None,
        stdout_lines: Optional[list[str]] = None,
        stderr_lines: Optional[list[str]] = None,
    ) -> None:
        self.result = result
        self.raises = raises
        self.stdout_lines = stdout_lines or []
        self.stderr_lines = stderr_lines or []
        self.last_request: Optional[ExecutionRequest] = None

    def run(self, request, stdout_handler=None, stderr_handler=None):
        self.last_request = request
        if self.raises is not None:
            raise self.raises
        if stdout_handler is not None:
            for line in self.stdout_lines:
                stdout_handler(line)
        if stderr_handler is not None:
            for line in self.stderr_lines:
                stderr_handler(line)
        return self.result


class _Recorder:
    """Collects every event published to a bus, in order."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def types(self) -> list[EventType]:
        return [event.event_type for event in self.events]


class KernelSuccessTests(unittest.TestCase):

    def test_successful_command_marks_cell_completed(self) -> None:
        bus = EventBus()
        runner = StubRunner(
            result=ExecutionResult(stdout="hi\n", stderr="", exit_code=0),
        )
        kernel = ExecutionKernel(bus, runner=runner)
        cell = Cell.new("echo hi")
        result = kernel.execute(cell)
        self.assertEqual(cell.status, CellStatus.COMPLETED)
        self.assertEqual(cell.stdout, "hi\n")
        self.assertEqual(cell.exit_code, 0)
        self.assertEqual(result.stdout, "hi\n")

    def test_lifecycle_event_order(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        runner = StubRunner(
            result=ExecutionResult(stdout="", stderr="", exit_code=0),
            stdout_lines=["one\n", "two\n"],
        )
        kernel = ExecutionKernel(bus, runner=runner)
        kernel.execute(Cell.new("noop"))
        self.assertEqual(
            recorder.types(),
            [
                EventType.COMMAND_SUBMITTED,
                EventType.COMMAND_STARTED,
                EventType.OUTPUT_CHUNK,
                EventType.OUTPUT_CHUNK,
                EventType.COMMAND_COMPLETED,
            ],
        )

    def test_output_chunk_payload_carries_line_and_cell_id(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        runner = StubRunner(
            result=ExecutionResult(stdout="", stderr="", exit_code=0),
            stdout_lines=["hello\n"],
        )
        kernel = ExecutionKernel(bus, runner=runner)
        cell = Cell.new("noop")
        kernel.execute(cell)
        chunks = [e for e in recorder.events if e.event_type == EventType.OUTPUT_CHUNK]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].payload["line"], "hello\n")
        self.assertEqual(chunks[0].payload["cell_id"], cell.cell_id)


class KernelFailureTests(unittest.TestCase):

    def test_nonzero_exit_is_failure(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        runner = StubRunner(
            result=ExecutionResult(stdout="", stderr="boom\n", exit_code=3),
            stderr_lines=["boom\n"],
        )
        kernel = ExecutionKernel(bus, runner=runner)
        cell = Cell.new("false")
        kernel.execute(cell)
        self.assertEqual(cell.status, CellStatus.FAILED)
        self.assertEqual(cell.exit_code, 3)
        self.assertIn(EventType.COMMAND_FAILED, recorder.types())
        self.assertIn(EventType.ERROR_CHUNK, recorder.types())

    def test_timeout_reports_failure(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        runner = StubRunner(
            result=ExecutionResult(stdout="", stderr="", exit_code=-9, timed_out=True),
        )
        kernel = ExecutionKernel(bus, runner=runner)
        cell = Cell.new("sleep 60")
        kernel.execute(cell, timeout_seconds=0.1)
        self.assertEqual(cell.status, CellStatus.FAILED)
        final = recorder.events[-1]
        self.assertEqual(final.event_type, EventType.COMMAND_FAILED)
        self.assertTrue(final.payload["timed_out"])

    def test_missing_executable_fails_gracefully(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        runner = StubRunner(raises=FileNotFoundError("no such binary"))
        kernel = ExecutionKernel(bus, runner=runner)
        cell = Cell.new("nope")
        result = kernel.execute(cell)
        self.assertEqual(cell.status, CellStatus.FAILED)
        self.assertEqual(cell.exit_code, EXIT_CODE_NOT_FOUND)
        self.assertEqual(result.exit_code, EXIT_CODE_NOT_FOUND)
        failed = [e for e in recorder.events if e.event_type == EventType.COMMAND_FAILED]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].payload["error_type"], "FileNotFoundError")

    def test_parse_error_fails_gracefully(self) -> None:
        bus = EventBus()
        runner = StubRunner(raises=ValueError("unbalanced quote"))
        kernel = ExecutionKernel(bus, runner=runner)
        cell = Cell.new('echo "oops')
        result = kernel.execute(cell)
        self.assertEqual(cell.status, CellStatus.FAILED)
        self.assertEqual(result.exit_code, EXIT_CODE_PARSE_ERROR)


class KernelConfigurationTests(unittest.TestCase):

    def test_default_mode_is_forwarded(self) -> None:
        bus = EventBus()
        runner = StubRunner(
            result=ExecutionResult(stdout="", stderr="", exit_code=0),
        )
        kernel = ExecutionKernel(bus, runner=runner, default_mode=ExecutionMode.SHELL)
        kernel.execute(Cell.new("echo hi"))
        assert runner.last_request is not None
        self.assertEqual(runner.last_request.mode, ExecutionMode.SHELL)

    def test_per_call_mode_overrides_default(self) -> None:
        bus = EventBus()
        runner = StubRunner(
            result=ExecutionResult(stdout="", stderr="", exit_code=0),
        )
        kernel = ExecutionKernel(bus, runner=runner, default_mode=ExecutionMode.ARGV)
        kernel.execute(Cell.new("echo hi"), mode=ExecutionMode.SHELL)
        assert runner.last_request is not None
        self.assertEqual(runner.last_request.mode, ExecutionMode.SHELL)


class KernelEndToEndTests(unittest.TestCase):

    def test_real_subprocess_pipeline(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        kernel = ExecutionKernel(bus, runner=ProcessRunner())
        cell = Cell.new(f'{sys.executable} -c "print(2+2)"')
        kernel.execute(cell)
        self.assertEqual(cell.status, CellStatus.COMPLETED)
        self.assertIn("4", cell.stdout)
        self.assertEqual(cell.exit_code, 0)
        self.assertIn(EventType.COMMAND_COMPLETED, recorder.types())


if __name__ == "__main__":
    unittest.main()
