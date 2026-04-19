"""CompletionFocusWatcher: nudge when a command completes while focus is away.

A blind user who starts a long-running command and then tabs to a
different cell to keep working can miss the normal completion cue —
it fires once, at conversational volume, and the user is already
typing into the new cell. This module publishes an additional
``COMMAND_COMPLETED_AWAY`` event in that case so the default bank
(or a user-authored bank) can bind it to a distinctive "hey, come
back" chime.

Semantics: every ``COMMAND_COMPLETED`` / ``COMMAND_FAILED`` event
carries the originating ``cell_id``. The watcher keeps a shadow of
the user's current focus cell (from ``FOCUS_CHANGED``). If the two
differ when completion fires, ``COMMAND_COMPLETED_AWAY`` is
published immediately *after* the original completion event. The
two-tier semantics keep the normal completion cue firing for
correctness; the away cue is a bonus nudge.

The watcher intentionally does not speak or chime on its own —
that remains the SoundEngine's job, driven by bindings. A user who
wants to silence the away alert simply disables the corresponding
binding in their bank.
"""

from __future__ import annotations

from typing import Optional

from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType


class CompletionFocusWatcher:
    """Publish COMMAND_COMPLETED_AWAY when completion fires away from focus."""

    SOURCE = "completion_alert"

    def __init__(self, bus: EventBus) -> None:
        """Subscribe to focus and completion events on the given bus."""
        self._bus = bus
        self._current_cell_id: Optional[str] = None
        bus.subscribe(EventType.FOCUS_CHANGED, self._on_focus_changed)
        bus.subscribe(EventType.COMMAND_COMPLETED, self._on_completion)
        bus.subscribe(EventType.COMMAND_FAILED, self._on_completion)

    @property
    def current_cell_id(self) -> Optional[str]:
        """Return the cell_id the user's focus last landed on (or None)."""
        return self._current_cell_id

    def _on_focus_changed(self, event: Event) -> None:
        """Track the cell the user is now focused on."""
        self._current_cell_id = event.payload.get("new_cell_id")

    def _on_completion(self, event: Event) -> None:
        """Publish COMMAND_COMPLETED_AWAY when focus differs from the runner.

        The watcher only fires the away event when there is something
        to compare against: an originating ``cell_id`` on the
        completion payload, and a known current focus cell. The
        ``original_event_type`` field lets a binding or a log reader
        distinguish "completed away" from "failed away" without
        subscribing to both original types separately.
        """
        origin = event.payload.get("cell_id")
        if not origin:
            return
        current = self._current_cell_id
        # No focus history yet — defaulting to "away" would flood the
        # very first command with a spurious nudge.
        if current is None:
            return
        if current == origin:
            return
        publish_event(
            self._bus,
            EventType.COMMAND_COMPLETED_AWAY,
            {
                "cell_id": origin,
                "current_cell_id": current,
                "original_event_type": event.event_type.value,
                "exit_code": event.payload.get("exit_code"),
                "timed_out": event.payload.get("timed_out"),
            },
            source=self.SOURCE,
        )
