"""ExecutionKernel: orchestrates running a Cell's command.

The kernel is the sole module that knows how to connect a Cell, the
ProcessRunner, and the EventBus. Everything else in ASAT interacts
with execution through the events the kernel publishes.

Event sequence for a normal run:
    COMMAND_SUBMITTED  (payload: cell_id, command)
    COMMAND_STARTED    (payload: cell_id)
    OUTPUT_CHUNK       (payload: cell_id, line)   zero or more
    ERROR_CHUNK        (payload: cell_id, line)   zero or more
    COMMAND_COMPLETED  (payload: cell_id, exit_code, timed_out)
    or
    COMMAND_FAILED     (payload: cell_id, exit_code, timed_out)
    or
    COMMAND_FAILED     (payload: cell_id, error, error_type) when the
        kernel could not launch the process at all (missing executable,
        unparseable command string).

Command exit codes that are non-zero are reported as COMMAND_FAILED
rather than COMMAND_COMPLETED so the audio engine can easily route
them to the error voice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from asat.cell import Cell
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.runner import ProcessRunner


EXIT_CODE_NOT_FOUND = 127
EXIT_CODE_PARSE_ERROR = 2


class ExecutionKernel:
    """Runs a Cell's command and records the outcome back on the cell."""

    SOURCE = "kernel"

    def __init__(
        self,
        event_bus: EventBus,
        runner: Optional[ProcessRunner] = None,
        default_mode: ExecutionMode = ExecutionMode.ARGV,
    ) -> None:
        """Store collaborators and the default execution mode.

        event_bus receives lifecycle and streaming events. runner is
        replaceable for testing. default_mode applies when the caller
        does not override the mode on a specific execute call.
        """
        self._bus = event_bus
        self._runner = runner if runner is not None else ProcessRunner()
        self._default_mode = default_mode

    def execute(
        self,
        cell: Cell,
        mode: Optional[ExecutionMode] = None,
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ExecutionResult:
        """Run the cell's command and update the cell in place.

        Returns the ExecutionResult for callers that want direct access
        to the outcome. The cell itself is mutated: command outputs
        and the final status are written to it regardless of whether
        the caller inspects the return value.
        """
        request = ExecutionRequest(
            command=cell.command,
            mode=mode if mode is not None else self._default_mode,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        self._publish(
            EventType.COMMAND_SUBMITTED,
            cell_id=cell.cell_id,
            command=cell.command,
        )
        cell.mark_running()
        self._publish(EventType.COMMAND_STARTED, cell_id=cell.cell_id)
        try:
            result = self._runner.run(
                request,
                stdout_handler=self._make_line_forwarder(cell.cell_id, EventType.OUTPUT_CHUNK),
                stderr_handler=self._make_line_forwarder(cell.cell_id, EventType.ERROR_CHUNK),
            )
        except FileNotFoundError as exc:
            return self._fail_before_launch(cell, exc, EXIT_CODE_NOT_FOUND)
        except ValueError as exc:
            return self._fail_before_launch(cell, exc, EXIT_CODE_PARSE_ERROR)

        cell.mark_completed(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )
        succeeded = result.exit_code == 0 and not result.timed_out
        final_type = EventType.COMMAND_COMPLETED if succeeded else EventType.COMMAND_FAILED
        self._publish(
            final_type,
            cell_id=cell.cell_id,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
        return result

    def _make_line_forwarder(self, cell_id: str, event_type: EventType):
        """Build a line handler that republishes each line as an event."""

        def forward(line: str) -> None:
            self._publish(event_type, cell_id=cell_id, line=line)

        return forward

    def _fail_before_launch(
        self,
        cell: Cell,
        exc: BaseException,
        exit_code: int,
    ) -> ExecutionResult:
        """Record a launch-time failure on the cell and publish an event.

        Used when the kernel could not even start the subprocess, for
        example because the executable does not exist or the command
        string could not be parsed.
        """
        message = str(exc)
        cell.mark_completed(stdout="", stderr=message, exit_code=exit_code)
        self._publish(
            EventType.COMMAND_FAILED,
            cell_id=cell.cell_id,
            error=message,
            error_type=exc.__class__.__name__,
            exit_code=exit_code,
            timed_out=False,
        )
        return ExecutionResult(
            stdout="",
            stderr=message,
            exit_code=exit_code,
            timed_out=False,
        )

    def _publish(self, event_type: EventType, **payload) -> None:
        """Publish an Event on the bus with the kernel as the source."""
        self._bus.publish(
            Event(event_type=event_type, payload=payload, source=self.SOURCE)
        )
