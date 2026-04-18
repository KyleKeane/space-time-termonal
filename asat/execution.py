"""Value objects for describing a command execution.

These dataclasses carry everything the low-level runner needs to spawn
a process, and everything the kernel needs to record the outcome on a
Cell. They are deliberately inert: no logic beyond field storage. The
runner and kernel modules consume them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class ExecutionMode(str, Enum):
    """How a command string is translated into arguments.

    ARGV: parse the command string with shlex into a strict argv list
        and call the executable directly. No shell interpreter is
        involved. Pipes, redirections, globbing, and variable
        expansion are not available. This is the safer default
        because no second interpreter ever sees the command.

    SHELL: hand the raw command string to the platform shell
        (/bin/sh -c on POSIX, cmd.exe /c on Windows). All shell
        features are available. A terminal user typing commands at
        their own prompt is the trust model, so this mode is
        legitimate despite the general warnings around shell=True.
    """

    ARGV = "argv"
    SHELL = "shell"


@dataclass(frozen=True)
class ExecutionRequest:
    """Parameters for a single command execution.

    command: The raw command string as typed by the user.
    mode: Whether to parse as argv or hand off to the shell.
    cwd: Working directory for the child process, or None to inherit.
    env: Environment mapping for the child process, or None to inherit.
    timeout_seconds: Wall-clock limit before the child is killed, or
        None for no timeout.
    """

    command: str
    mode: ExecutionMode = ExecutionMode.ARGV
    cwd: Optional[Path] = None
    env: Optional[dict[str, str]] = None
    timeout_seconds: Optional[float] = None


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of a single command execution.

    stdout: Complete captured standard output as a single string.
    stderr: Complete captured standard error as a single string.
    exit_code: Process exit code. Negative values on POSIX indicate
        termination by a signal (see subprocess docs). The kernel
        uses 127 for 'command not found' and 2 for 'parse error' to
        mirror shell conventions.
    timed_out: True if the process was killed by the timeout enforcer
        rather than exiting on its own.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
