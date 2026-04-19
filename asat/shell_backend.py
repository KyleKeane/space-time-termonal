"""Persistent shell backend: one long-lived shell per session.

ASAT's `ProcessRunner` spawns a fresh `subprocess.Popen` per cell, so
state never carries: `cd /tmp` in cell 1 does not change the cwd of
cell 2, `export X=1` in cell 1 leaves cell 2 with no `$X`. This module
introduces an alternative runner — `ShellBackend` — that holds one
long-lived shell process for the whole session and threads each cell's
command through it via the shell's own stdin. The shell sees a single
session of commands, exactly as a human typing into a terminal would,
so all the state a real shell maintains (cwd, exported variables,
function definitions, set options, alias definitions) carries between
cells naturally.

The backend mirrors `ProcessRunner.run`'s contract — same arguments,
same `ExecutionResult` return — so the kernel can swap one for the
other without further changes. The trade-off is that a long-lived
shell amplifies blast radius: a `set -e`, a stuck `read`, or a
runaway `while true` cannot be killed by waiting for the next
command's `subprocess.wait()` to return; the backend handles those
cases by enforcing the request's `timeout_seconds` and, on timeout,
sending SIGINT into the shell to break the running command without
killing the shell itself.

Sentinel framing
----------------
Each command submission writes three lines to the shell's stdin:

    <command>
    printf "\\n__ASAT_SHELL_END_<uuid>__:%d\\n" "$?"
    printf "__ASAT_SHELL_ERR_<uuid>__\\n" >&2

The reader threads (one per stream) push lines into queues until they
see the sentinel for their stream, then signal an event. `run()`
collects everything that arrived on stdout / stderr before the
sentinel, returns it as a single `ExecutionResult`, and the next
command starts cleanly. Sentinels never reach the caller — they are
stripped at the reader-thread layer.

POSIX only in this first cut. A `ShellBackend(shell="cmd")` Windows
adapter is a separate follow-up (the sentinel approach works the
same way; only the framing strings change).
"""

from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import threading
import uuid
from io import StringIO
from typing import Optional

from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.runner import LineHandler


_SENTINEL_OUT_PREFIX = "__ASAT_SHELL_END_"
_SENTINEL_ERR_PREFIX = "__ASAT_SHELL_ERR_"


class ShellBackendError(RuntimeError):
    """Raised when the backing shell process is unusable.

    Two distinct conditions raise this: (1) the shell exits between
    commands, leaving the backend with no process to dispatch the
    next request to; (2) the shell crashes mid-command, before both
    sentinel markers have arrived. Callers above the kernel should
    catch this and decide whether to surface a `BACKEND_EXITED`
    event and restart, or to give up.
    """


class ShellBackend:
    """A long-lived shell that ASAT routes every cell command through.

    Construction launches the shell. Every subsequent `run()` call
    writes one command to the shell's stdin, waits for the sentinel
    pair, and returns the captured stdout / stderr / exit code.
    `close()` exits the shell cleanly. The backend serialises
    submissions internally — concurrent `run()` calls block on a
    lock — because a single shell can only execute one command at a
    time anyway.
    """

    SHELL_DEFAULT_ARGV = ("bash", "--norc", "--noprofile")

    def __init__(
        self,
        shell: Optional[tuple[str, ...]] = None,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """Spawn the backing shell process.

        `shell` is the argv tuple used to launch the shell. Defaults
        to `bash --norc --noprofile` so the user's dotfiles do not
        interfere with the sentinel protocol. `env` and `cwd` are
        passed through to `subprocess.Popen`; pass `None` to inherit
        the parent's environment / working directory.

        The shell is launched in its own session (`start_new_session
        =True`) so SIGINT during a timeout reaches the running
        foreground child via `killpg`, and `trap : INT` is set on
        the shell itself so the same SIGINT runs a no-op handler in
        bash (which therefore survives) but resets to the default
        disposition in any child process across `exec` — so the
        running command exits with 130, exactly like Ctrl+C at a
        real prompt. (We use a `:` handler rather than `trap ''
        INT`; the latter would set SIG_IGN, which `exec` *preserves*
        in children, so the running command would also ignore the
        signal.)

        Raises `ShellBackendError` if the named shell is not on PATH
        — the fallback chain (bash → sh) is the caller's
        responsibility, not the backend's.
        """
        argv = list(shell) if shell is not None else list(self.SHELL_DEFAULT_ARGV)
        if shutil.which(argv[0]) is None:
            raise ShellBackendError(f"shell {argv[0]!r} not found on PATH")
        self._argv = tuple(argv)
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._lock = threading.Lock()
        self._stdout_queue: queue.Queue[Optional[tuple[str, str]]] = queue.Queue()
        self._stderr_queue: queue.Queue[Optional[tuple[str, str]]] = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._pump,
            args=(self._proc.stdout, self._stdout_queue, _SENTINEL_OUT_PREFIX),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._pump,
            args=(self._proc.stderr, self._stderr_queue, _SENTINEL_ERR_PREFIX),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        # Install a no-op SIGINT handler in the shell so it survives a
        # timeout-driven killpg(SIGINT). `exec` in the foreground child
        # resets the handler to SIG_DFL, so the child still takes the
        # signal and exits 130 as expected.
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write("trap : INT\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise ShellBackendError("shell backend stdin closed at startup") from exc

    @property
    def argv(self) -> tuple[str, ...]:
        """The argv used to launch the backing shell, for diagnostics."""
        return self._argv

    def is_alive(self) -> bool:
        """Return True if the backing shell process is still running."""
        return self._proc.poll() is None

    def run(
        self,
        request: ExecutionRequest,
        stdout_handler: Optional[LineHandler] = None,
        stderr_handler: Optional[LineHandler] = None,
    ) -> ExecutionResult:
        """Execute one command in the persistent shell.

        Mirrors `ProcessRunner.run`'s signature so the kernel can
        treat `ShellBackend` as a drop-in replacement. `request.mode`
        is ignored — the backend is intrinsically a shell, so every
        command is shell-evaluated. `request.cwd` and `request.env`
        are also ignored on a per-call basis: the shell carries its
        own cwd and env across calls (that is the whole point), and
        per-call overrides would defeat the persistence model. Use
        `cd` and `export` inside the cell to change them.

        `request.timeout_seconds`, when set, fires SIGINT into the
        shell after the deadline — interrupting the running command
        without killing the shell, exactly like Ctrl+C at a real
        prompt. The sentinel still arrives once the interrupted
        command's wrapper code runs.
        """
        if not request.command.strip():
            raise ValueError("Command is empty")
        if not self.is_alive():
            raise ShellBackendError("shell backend is not running")
        with self._lock:
            return self._run_locked(request, stdout_handler, stderr_handler)

    def cancel(self) -> bool:
        """Send SIGINT to the foreground command without killing the shell.

        F1: gives the user a Ctrl+C-equivalent for the running cell.
        Reuses the same `killpg` plumbing the timeout path uses — the
        shell's `trap : INT` handler keeps the long-lived shell alive
        while the foreground child takes the default disposition and
        exits with 130. Returns True when a signal was delivered,
        False when the shell is no longer running.
        """
        if not self.is_alive():
            return False
        try:
            os.killpg(self._proc.pid, signal.SIGINT)
        except (ProcessLookupError, OSError):
            return False
        return True

    def close(self) -> None:
        """Exit the shell cleanly; force-terminate after a short grace."""
        if self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write("exit\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        # Always close the pipe handles even when the shell has
        # already exited on its own — otherwise the still-open stdin
        # FD trips ResourceWarning under unittest.
        for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        # Reader threads exit on EOF; join briefly so tests are clean.
        self._stdout_thread.join(timeout=1.0)
        self._stderr_thread.join(timeout=1.0)

    def _run_locked(
        self,
        request: ExecutionRequest,
        stdout_handler: Optional[LineHandler],
        stderr_handler: Optional[LineHandler],
    ) -> ExecutionResult:
        """Inner run loop; caller holds `self._lock`."""
        token = uuid.uuid4().hex
        sentinel_out = f"{_SENTINEL_OUT_PREFIX}{token}__"
        sentinel_err = f"{_SENTINEL_ERR_PREFIX}{token}__"
        framed = (
            f"{request.command}\n"
            f'printf "\\n{sentinel_out}:%d\\n" "$?"\n'
            f'printf "{sentinel_err}\\n" >&2\n'
        )
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(framed)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise ShellBackendError("shell backend stdin closed") from exc

        stdout_buf = StringIO()
        stderr_buf = StringIO()
        timed_out = False
        deadline_thread: Optional[threading.Thread] = None
        if request.timeout_seconds is not None:
            timer = threading.Timer(request.timeout_seconds, self.cancel)
            timer.daemon = True
            timer.start()
            deadline_thread = timer

        exit_code = self._collect(
            self._stdout_queue,
            sentinel_out,
            stdout_buf,
            stdout_handler,
            expect_exit_code=True,
        )
        self._collect(
            self._stderr_queue,
            sentinel_err,
            stderr_buf,
            stderr_handler,
            expect_exit_code=False,
        )

        if deadline_thread is not None:
            deadline_thread.cancel()
            # An interrupted command exits with 130 (128 + SIGINT) on bash;
            # surface that as timed_out=True so the caller's audio routing
            # matches the existing ProcessRunner timeout contract.
            if exit_code == 130:
                timed_out = True

        return ExecutionResult(
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            exit_code=exit_code,
            timed_out=timed_out,
        )

    def _collect(
        self,
        source: "queue.Queue[Optional[tuple[str, str]]]",
        sentinel: str,
        buffer: StringIO,
        handler: Optional[LineHandler],
        *,
        expect_exit_code: bool,
    ) -> int:
        """Drain a stream queue until the sentinel arrives.

        Returns the parsed exit code when `expect_exit_code` is True
        (stdout sentinel carries `:<n>` after it). For the stderr
        path the value is meaningless and 0 is returned.
        """
        while True:
            item = source.get()
            if item is None:
                raise ShellBackendError(
                    "shell backend stream closed before command completed"
                )
            kind, line = item
            if kind == "sentinel":
                if expect_exit_code:
                    try:
                        return int(line.split(":", 1)[1])
                    except (IndexError, ValueError) as exc:
                        raise ShellBackendError(
                            f"malformed sentinel: {line!r}"
                        ) from exc
                return 0
            buffer.write(line)
            if handler is not None:
                handler(line)

    @staticmethod
    def _pump(
        stream,
        sink: "queue.Queue[Optional[tuple[str, str]]]",
        sentinel_prefix: str,
    ) -> None:
        """Read one stream until EOF, tagging sentinel lines for the collector.

        Lines whose first token starts with `sentinel_prefix` are
        forwarded as `("sentinel", <full-line-without-newline>)`.
        Every other line is forwarded as `("line", <line>)` with
        its trailing newline preserved. EOF emits a single `None`
        so the collector can detect the shell crashing.

        One previous line is buffered so we can drop the single
        framing newline that precedes every sentinel — `printf
        "\\n<sentinel>..."` writes that `\\n` so the sentinel is
        guaranteed to start at column 0 even when the user command
        ended without a trailing newline. Without the buffer that
        framing `\\n` would surface to callers as a phantom empty
        line at the end of every command's output.
        """
        if stream is None:
            sink.put(None)
            return
        pending: Optional[str] = None
        try:
            for raw in stream:
                stripped = raw.rstrip("\n")
                if stripped.startswith(sentinel_prefix):
                    if pending is not None and pending != "\n":
                        sink.put(("line", pending))
                    pending = None
                    sink.put(("sentinel", stripped))
                    continue
                if pending is not None:
                    sink.put(("line", pending))
                pending = raw
        finally:
            if pending is not None:
                sink.put(("line", pending))
            sink.put(None)
            try:
                stream.close()
            except Exception:
                pass


def shell_backend_or_none(
    shell: Optional[tuple[str, ...]] = None,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> Optional[ShellBackend]:
    """Try to construct a ShellBackend; return None if not feasible.

    Used by the CLI / `Application.build` to opportunistically enable
    persistent-shell mode without crashing on systems where bash is
    missing (Windows out of the box, stripped containers). Falls back
    to the per-cell `ProcessRunner` model when None is returned.
    """
    if os.name == "nt":
        return None
    try:
        return ShellBackend(shell=shell, env=env, cwd=cwd)
    except ShellBackendError:
        return None
