"""PromptContext: attach trailing exit-code + CWD to INPUT-mode entries.

The `[input #…]` banner that fires on every FOCUS_CHANGED tells the
user they are in a fresh cell but carries no audit of the command
they just ran. A blind user has no quick cue for "the last thing
failed" or "you're now in a different directory".

PromptContext watches two streams on the bus:

- COMMAND_COMPLETED / COMMAND_FAILED — to remember the last exit code.
- FOCUS_CHANGED with `new_mode == input` — the trigger to publish a
  PROMPT_REFRESH event carrying that trailing context.

Consumers react to PROMPT_REFRESH rather than re-deriving the state
themselves: the TerminalRenderer prints a compact one-liner, and the
default SoundBank binds it to a short spoken summary via the `system`
voice.

The module is intentionally a thin coordinator. No I/O, no threading,
no persistence. `cwd_provider` is injectable so tests can pin the
value; the default uses `os.getcwd`.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType


class PromptContext:
    """Aggregate exit-code + CWD and republish on every INPUT entry."""

    SOURCE = "prompt_context"

    def __init__(
        self,
        bus: EventBus,
        *,
        cwd_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        """Attach to `bus` and start tracking completion + focus events.

        `cwd_provider` defaults to `os.getcwd`. Override for tests or
        for a future feature that wants a virtual CWD (a sandbox, a
        chroot, etc.).
        """
        self._bus = bus
        self._cwd_provider = cwd_provider if cwd_provider is not None else os.getcwd
        self._last_exit_code: Optional[int] = None
        self._last_cell_id: Optional[str] = None
        self._last_timed_out: bool = False
        bus.subscribe(EventType.COMMAND_COMPLETED, self._on_command_finished)
        bus.subscribe(EventType.COMMAND_FAILED, self._on_command_finished)
        bus.subscribe(EventType.FOCUS_CHANGED, self._on_focus_changed)

    @property
    def last_exit_code(self) -> Optional[int]:
        """Return the exit code of the most recently finished command, or None."""
        return self._last_exit_code

    @property
    def last_cell_id(self) -> Optional[str]:
        """Return the cell id of the most recently finished command, or None."""
        return self._last_cell_id

    def refresh(self) -> None:
        """Explicitly publish a PROMPT_REFRESH (used on INPUT transitions)."""
        if self._last_exit_code is None:
            # Nothing to say yet — no command has completed since launch.
            # Publishing here would make the renderer spam `[prompt ...]`
            # lines on every new cell before the first run.
            return
        publish_event(
            self._bus,
            EventType.PROMPT_REFRESH,
            {
                "last_exit_code": self._last_exit_code,
                "last_cell_id": self._last_cell_id,
                "last_timed_out": self._last_timed_out,
                "cwd": self._cwd_provider(),
            },
            source=self.SOURCE,
        )

    def _on_command_finished(self, event: Event) -> None:
        """Record the exit code so the next INPUT entry can report it."""
        payload = event.payload
        exit_code = payload.get("exit_code")
        if isinstance(exit_code, int):
            self._last_exit_code = exit_code
        cell_id = payload.get("cell_id")
        if isinstance(cell_id, str):
            self._last_cell_id = cell_id
        timed_out = payload.get("timed_out")
        self._last_timed_out = bool(timed_out)

    def _on_focus_changed(self, event: Event) -> None:
        """Fire PROMPT_REFRESH when the user lands in INPUT mode."""
        if event.payload.get("new_mode") != "input":
            return
        self.refresh()
