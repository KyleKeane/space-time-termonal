"""Low-level subprocess runner with streamed output capture.

The ProcessRunner is the only place in ASAT that directly touches the
subprocess module. Everything above it (the kernel, the future parsers
and audio engine) consumes ExecutionResult objects and LineHandler
callbacks. Keeping this module thin makes it easy to audit the one
spot where untrusted command strings become running processes.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from io import StringIO
from typing import Callable, Optional

from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult


LineHandler = Callable[[str], None]


class ProcessRunner:
    """Synchronous runner that streams stdout and stderr line by line.

    The run method blocks until the process exits or the timeout fires.
    Two background threads drain the child's stdout and stderr pipes
    independently so a flood of output on one stream cannot deadlock
    the other. This is the standard portable pattern for concurrent
    pipe reading from subprocess.Popen.
    """

    def run(
        self,
        request: ExecutionRequest,
        stdout_handler: Optional[LineHandler] = None,
        stderr_handler: Optional[LineHandler] = None,
    ) -> ExecutionResult:
        """Execute the request and return its ExecutionResult.

        stdout_handler and stderr_handler, if provided, are invoked on
        the reader threads with each line as it arrives. Handlers must
        be thread-safe with respect to any shared state they touch;
        the runner itself serializes nothing on their behalf.
        """
        argv, use_shell = self._build_argv(request)
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(request.cwd) if request.cwd is not None else None,
            env=request.env,
            shell=use_shell,
            text=True,
            bufsize=1,
        )
        stdout_buffer = StringIO()
        stderr_buffer = StringIO()
        stdout_thread = self._spawn_pump(process.stdout, stdout_buffer, stdout_handler)
        stderr_thread = self._spawn_pump(process.stderr, stderr_buffer, stderr_handler)
        timed_out = False
        try:
            exit_code = process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = process.wait()
            timed_out = True
        stdout_thread.join()
        stderr_thread.join()
        return ExecutionResult(
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            exit_code=exit_code,
            timed_out=timed_out,
        )

    @staticmethod
    def _build_argv(request: ExecutionRequest):
        """Return the (argv, shell_flag) pair for subprocess.Popen.

        Raises ValueError on an empty command or a shlex parse failure.
        """
        if request.mode == ExecutionMode.SHELL:
            if not request.command.strip():
                raise ValueError("Command is empty")
            return request.command, True
        argv = shlex.split(request.command)
        if not argv:
            raise ValueError("Command is empty")
        return argv, False

    @staticmethod
    def _spawn_pump(stream, buffer: StringIO, handler: Optional[LineHandler]) -> threading.Thread:
        """Start a daemon thread that drains stream into buffer."""
        thread = threading.Thread(
            target=ProcessRunner._pump,
            args=(stream, buffer, handler),
            daemon=True,
        )
        thread.start()
        return thread

    @staticmethod
    def _pump(stream, buffer: StringIO, handler: Optional[LineHandler]) -> None:
        """Read lines from stream until EOF, mirroring into buffer and handler."""
        if stream is None:
            return
        try:
            for line in stream:
                buffer.write(line)
                if handler is not None:
                    handler(line)
        finally:
            stream.close()
