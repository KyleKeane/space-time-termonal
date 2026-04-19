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
    or
    COMMAND_CANCELLED  (payload: cell_id, exit_code) when `cancel()`
        was called from another thread (F1 — Ctrl+C in INPUT mode)
        while the runner was waiting on the subprocess.

Command exit codes that are non-zero are reported as COMMAND_FAILED
rather than COMMAND_COMPLETED so the audio engine can easily route
them to the error voice.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from asat.cell import Cell
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.runner import ProcessRunner
from asat.shell_backend import ShellBackendError


EXIT_CODE_NOT_FOUND = 127
EXIT_CODE_PARSE_ERROR = 2
# Bash convention: 125 is the "command itself failed to launch" rung
# below "127 not found" and "126 not executable". We reuse it for the
# persistent shell crashing mid-command — distinct from any exit code
# the user's command could plausibly emit.
EXIT_CODE_BACKEND_ERROR = 125


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
        # F1 cancel-coordination state. `_active_cell_id` is non-None
        # only while a cell is mid-`execute`, so `cancel(cell_id)`
        # can verify the request matches what's actually running.
        # `_cancelled_cells` records ids the user asked to cancel
        # before the runner returned; the post-run check consumes the
        # marker and converts the result into COMMAND_CANCELLED.
        self._cancel_lock = threading.Lock()
        self._active_cell_id: Optional[str] = None
        self._cancelled_cells: set[str] = set()

    @property
    def active_cell_id(self) -> Optional[str]:
        """Return the id of the cell currently executing, or None."""
        with self._cancel_lock:
            return self._active_cell_id

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
        with self._cancel_lock:
            self._active_cell_id = cell.cell_id
            # A cancel request that arrived before this execute() call
            # is consumed by the post-run check; clearing leftovers
            # keeps a stale id from short-circuiting a fresh run.
            self._cancelled_cells.discard(cell.cell_id)
        try:
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
            except ShellBackendError as exc:
                # The persistent shell crashed mid-command (or was already
                # dead). Surface as a launch-time failure so the user gets
                # the same audio cue and stderr-tail narration they'd get
                # for any other failed command. Restart of the backend is
                # the caller's job; the kernel just records what happened.
                return self._fail_before_launch(cell, exc, EXIT_CODE_BACKEND_ERROR)

            with self._cancel_lock:
                was_cancelled = cell.cell_id in self._cancelled_cells
                self._cancelled_cells.discard(cell.cell_id)
            if was_cancelled:
                # Record on the cell as cancelled, but persist the partial
                # output the runner already collected so the user can review
                # what landed before they hit Ctrl+C.
                cell.stdout = result.stdout
                cell.stderr = result.stderr
                cell.exit_code = result.exit_code
                cell.mark_cancelled()
                self._publish(
                    EventType.COMMAND_CANCELLED,
                    cell_id=cell.cell_id,
                    exit_code=result.exit_code,
                )
                return result

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
        finally:
            with self._cancel_lock:
                if self._active_cell_id == cell.cell_id:
                    self._active_cell_id = None

    def cancel(self, cell_id: str) -> bool:
        """Cancel the cell currently running, if its id matches `cell_id`.

        Returns True when a cancel signal was actually delivered to the
        runner and the post-run path will publish `COMMAND_CANCELLED`;
        False when no matching cell is running (nothing in flight, or a
        different cell). The kernel relays the signal via the runner's
        `cancel()` method (`ProcessRunner` SIGTERMs the subprocess;
        `ShellBackend` SIGINTs the foreground child via killpg). When
        the runner exposes no `cancel`, the call still records the
        intent so a long-running request returning naturally still
        narrates as a cancellation rather than a generic failure.
        """
        with self._cancel_lock:
            if self._active_cell_id != cell_id:
                return False
            self._cancelled_cells.add(cell_id)
            runner_cancel = getattr(self._runner, "cancel", None)
        if runner_cancel is not None:
            runner_cancel()
        return True

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
        string could not be parsed. The error message is also emitted
        as an ERROR_CHUNK so it flows through the normal stderr path
        (populating OutputBuffer for OUTPUT-mode review and feeding
        the F36 stderr-tail announcer).
        """
        message = str(exc)
        cell.mark_completed(stdout="", stderr=message, exit_code=exit_code)
        if message:
            self._publish(EventType.ERROR_CHUNK, cell_id=cell.cell_id, line=message)
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
        publish_event(self._bus, event_type, payload, source=self.SOURCE)
