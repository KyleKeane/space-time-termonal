"""TerminalRenderer: a minimal text view of the event stream.

The audio pipeline is the primary UI; this renderer exists so sighted
developers can see what ASAT is doing, and so `python -m asat` feels
like a terminal rather than a silent black hole. Every line it writes
is derived from one event on the bus.

What gets printed:

- `SESSION_CREATED`: one-line banner naming the session and pointing
  at `:help`.
- `HELP_REQUESTED`: the cheat-sheet lines carried on the event.
- `FOCUS_CHANGED`: a one-line status like `[input #1]` whenever the
  mode or focused cell changes.
- `ACTION_INVOKED` with action == `insert_character`: the literal
  character (so the user sees their typing, because cbreak disabled
  echo).
- `ACTION_INVOKED` with action == `backspace` in INPUT mode: `\\b \\b`
  to visually erase the last echoed character.
- `OUTPUT_CHUNK`: the captured stdout line.
- `ERROR_CHUNK`: the captured stderr line, prefixed with `! ` so the
  sighted viewer can tell them apart.
- `COMMAND_COMPLETED` / `COMMAND_FAILED`: one-line summary with the
  exit code.
- `SESSION_SAVED`: one-line confirmation.

The renderer intentionally does NOT render spatial hints or timing;
that's the audio engine's job. The point is "enough text so the user
is not flying blind." A `--quiet` switch in the CLI disables it.
"""

from __future__ import annotations

import sys
from typing import TextIO

from asat.event_bus import EventBus
from asat.events import Event, EventType


class TerminalRenderer:
    """Subscribe to the bus and write a minimal visible trace to a stream."""

    def __init__(self, bus: EventBus, stream: TextIO | None = None) -> None:
        """Attach to the bus and remember where to write (defaults to stdout)."""
        self._bus = bus
        self._stream = stream if stream is not None else sys.stdout
        self._at_line_start = True
        bus.subscribe(EventType.SESSION_CREATED, self._on_session_created)
        bus.subscribe(EventType.SESSION_LOADED, self._on_session_loaded)
        bus.subscribe(EventType.SESSION_SAVED, self._on_session_saved)
        bus.subscribe(EventType.HELP_REQUESTED, self._on_help_requested)
        bus.subscribe(EventType.FOCUS_CHANGED, self._on_focus_changed)
        bus.subscribe(EventType.ACTION_INVOKED, self._on_action_invoked)
        bus.subscribe(EventType.OUTPUT_CHUNK, self._on_output_chunk)
        bus.subscribe(EventType.ERROR_CHUNK, self._on_error_chunk)
        bus.subscribe(EventType.COMMAND_COMPLETED, self._on_command_completed)
        bus.subscribe(EventType.COMMAND_FAILED, self._on_command_failed)
        bus.subscribe(EventType.PROMPT_REFRESH, self._on_prompt_refresh)
        bus.subscribe(EventType.FIRST_RUN_DETECTED, self._on_first_run)

    def _write_line(self, text: str) -> None:
        """Print a full line, ending any in-flight keystroke echo first."""
        if not self._at_line_start:
            self._stream.write("\n")
        self._stream.write(text)
        self._stream.write("\n")
        self._stream.flush()
        self._at_line_start = True

    def _write_inline(self, text: str) -> None:
        """Echo raw characters without a trailing newline (for typing feedback)."""
        self._stream.write(text)
        self._stream.flush()
        self._at_line_start = False

    def _on_session_created(self, event: Event) -> None:
        session_id = event.payload.get("session_id", "?")
        self._write_line(
            f"[asat] session {session_id} ready. Type :help for the keystroke "
            "cheat sheet, :quit to exit."
        )

    def _on_help_requested(self, event: Event) -> None:
        lines = event.payload.get("lines", [])
        for line in lines:
            self._write_line(str(line))

    def _on_session_loaded(self, event: Event) -> None:
        path = event.payload.get("path", "?")
        self._write_line(f"[asat] loaded session from {path}.")

    def _on_session_saved(self, event: Event) -> None:
        path = event.payload.get("path", "?")
        self._write_line(f"[asat] saved session to {path}.")

    def _on_focus_changed(self, event: Event) -> None:
        mode = event.payload.get("new_mode", "?")
        cell_id = event.payload.get("new_cell_id")
        suffix = f" #{cell_id[:6]}" if isinstance(cell_id, str) else ""
        self._write_line(f"[{mode}{suffix}]")

    def _on_action_invoked(self, event: Event) -> None:
        action = event.payload.get("action")
        if action == "insert_character":
            char = event.payload.get("char", "")
            if isinstance(char, str) and char:
                self._write_inline(char)
        elif action == "backspace":
            # Visually undo the last echoed character; harmless if the
            # cell already had no echoed content (the terminal just
            # beeps or clamps).
            self._write_inline("\b \b")
        elif action == "submit":
            if event.payload.get("meta_command") is None:
                command = event.payload.get("command", "")
                self._write_line(f"$ {command}")

    def _on_output_chunk(self, event: Event) -> None:
        line = event.payload.get("line", "")
        self._write_line(str(line))

    def _on_error_chunk(self, event: Event) -> None:
        line = event.payload.get("line", "")
        self._write_line(f"! {line}")

    def _on_command_completed(self, event: Event) -> None:
        exit_code = event.payload.get("exit_code", 0)
        self._write_line(f"[done exit={exit_code}]")

    def _on_command_failed(self, event: Event) -> None:
        payload = event.payload
        if "error" in payload:
            self._write_line(f"[failed: {payload['error']}]")
        else:
            exit_code = payload.get("exit_code", "?")
            self._write_line(f"[failed exit={exit_code}]")

    def _on_prompt_refresh(self, event: Event) -> None:
        payload = event.payload
        exit_code = payload.get("last_exit_code", "?")
        cwd = payload.get("cwd", "?")
        self._write_line(f"[prompt exit={exit_code} cwd={cwd}]")

    def _on_first_run(self, event: Event) -> None:
        lines = event.payload.get("lines", [])
        for line in lines:
            self._write_line(str(line))
