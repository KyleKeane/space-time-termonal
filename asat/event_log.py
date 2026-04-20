"""EventLogViewer: the interactive event log window (F39).

The audio pipeline narrates events as they fly past; the log viewer
is the user's rewind button and debugging lens. A wildcard
subscriber keeps the last ``max_entries`` events in a ring buffer —
every event, not just the ones that produced speech — so the
viewer can answer "what happened in the last second?" even when
the narrator chose to stay silent.

While the viewer is in ``FocusMode.EVENT_LOG``, the input router
dispatches arrow keys and action keys ``e`` (quick-edit) and ``t``
(replay) to the viewer's navigation / editing / replay API. The
viewer publishes its own ``EVENT_LOG_*`` family so the sound bank
can narrate the window ("event log, 7 entries, latest: audio
spoken, binding command_completed, say done exit 0"), the
terminal renderer can paint a dedicated panel, and tests can
assert the state machine without reaching into private fields.

The viewer's own events are tagged with ``source=EventLogViewer.SOURCE``
and filtered out of the ring so pressing Ctrl+E does not flood the
log with its own open/close entries — otherwise a single keystroke
would push the interesting tail off the bottom of the buffer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Optional

from asat.event_bus import WILDCARD, EventBus, publish_event
from asat.events import Event, EventType
from asat.sound_bank import EventBinding, SoundBankError
from asat.sound_engine import SoundEngine


DEFAULT_MAX_ENTRIES = 200

# Fields a quick-edit sub-mode cycles through, in order. Each is a
# simple text edit; structural fields (predicate, voice_overrides) are
# out of scope because they require the full settings editor.
QUICK_EDIT_FIELDS: tuple[str, ...] = (
    "say_template",
    "voice_id",
    "enabled",
    "priority",
)


@dataclass
class EventLogEntry:
    """A snapshot of one Event as seen by the viewer.

    ``narration`` is a short human-readable string the renderer /
    narrator can speak; ``binding_id`` is pulled from the
    ``AUDIO_SPOKEN`` payload when the originating event rendered
    speech, so pressing Enter on the entry can jump to that binding
    in the settings editor. ``payload`` is a defensive copy so
    mutations on the source dict do not leak into history.
    """

    event_type: str
    source: str
    timestamp: datetime
    payload: dict[str, Any]
    narration: str
    binding_id: Optional[str] = None

    @classmethod
    def from_event(cls, event: Event) -> "EventLogEntry":
        payload = dict(event.payload)
        binding_id = None
        if event.event_type is EventType.AUDIO_SPOKEN:
            binding_id = payload.get("binding_id")
        narration = _narrate_event(event.event_type.value, payload)
        return cls(
            event_type=event.event_type.value,
            source=event.source,
            timestamp=event.timestamp,
            payload=payload,
            narration=narration,
            binding_id=binding_id if isinstance(binding_id, str) else None,
        )


def _narrate_event(event_type: str, payload: dict[str, Any]) -> str:
    """Build a one-line narrator string for ``event_type`` + payload.

    The format is intentionally repetitive so a screen reader user
    can predict where to find each field: ``"<event_type>: <key>=…,
    <key>=…"``. We surface a handful of high-value keys per event
    family; unrecognised types get ``event_type`` on its own.
    """
    if event_type == EventType.AUDIO_SPOKEN.value:
        binding = payload.get("binding_id", "?")
        text = payload.get("text", "")
        return f"audio spoken, binding {binding}, say {text!r}"
    if event_type == EventType.OUTPUT_CHUNK.value:
        return f"output chunk: {payload.get('line', '')!r}"
    if event_type == EventType.ERROR_CHUNK.value:
        return f"error chunk: {payload.get('line', '')!r}"
    if event_type == EventType.COMMAND_COMPLETED.value:
        return f"command completed, exit={payload.get('exit_code', '?')}"
    if event_type == EventType.COMMAND_FAILED.value:
        return f"command failed, exit={payload.get('exit_code', '?')}"
    if event_type == EventType.FOCUS_CHANGED.value:
        return f"focus changed to {payload.get('new_mode', '?')}"
    if event_type == EventType.KEY_PRESSED.value:
        return f"key pressed: {payload.get('name', payload.get('char', '?'))}"
    return event_type


class EventLogError(RuntimeError):
    """Raised when a quick-edit or replay cannot be performed."""


class EventLogViewer:
    """Interactive viewer over a bounded ring of recent events."""

    SOURCE = "event_log_viewer"

    def __init__(
        self,
        bus: EventBus,
        sound_engine: SoundEngine,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._bus = bus
        self._sound_engine = sound_engine
        self._entries: Deque[EventLogEntry] = deque(maxlen=max_entries)
        self._focus_index: Optional[int] = None
        self._open = False
        self._quick_edit_field: Optional[str] = None
        self._quick_edit_buffer: str = ""
        self._quick_edit_binding_id: Optional[str] = None
        bus.subscribe(WILDCARD, self._on_event)

    # ------------------------------------------------------------------
    # Ring-buffer state
    # ------------------------------------------------------------------
    @property
    def entries(self) -> tuple[EventLogEntry, ...]:
        """Return a snapshot tuple of the current ring buffer."""
        return tuple(self._entries)

    @property
    def focus_index(self) -> Optional[int]:
        """Index of the focused entry (0-based from oldest), or None."""
        return self._focus_index

    @property
    def is_open(self) -> bool:
        """True while the viewer is in focus (``FocusMode.EVENT_LOG``)."""
        return self._open

    @property
    def quick_edit_field(self) -> Optional[str]:
        return self._quick_edit_field

    @property
    def quick_edit_buffer(self) -> str:
        return self._quick_edit_buffer

    def selected_entry(self) -> Optional[EventLogEntry]:
        if self._focus_index is None:
            return None
        if not 0 <= self._focus_index < len(self._entries):
            return None
        return self._entries[self._focus_index]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def open(self) -> None:
        """Enter viewer mode and focus the most recent entry.

        Re-entry is idempotent; an already-open viewer simply
        re-anchors focus on the latest entry so the user hears the
        newest event first after each Ctrl+E press.
        """
        self._open = True
        self._cancel_quick_edit_state()
        self._focus_index = len(self._entries) - 1 if self._entries else None
        publish_event(
            self._bus,
            EventType.EVENT_LOG_OPENED,
            {
                "count": len(self._entries),
                "focus_index": self._focus_index,
                "summary": self._summary_line(),
            },
            source=self.SOURCE,
        )

    def close(self) -> None:
        """Leave viewer mode."""
        if not self._open:
            return
        self._open = False
        self._cancel_quick_edit_state()
        publish_event(
            self._bus,
            EventType.EVENT_LOG_CLOSED,
            {"count": len(self._entries)},
            source=self.SOURCE,
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def focus_latest(self) -> None:
        if not self._entries:
            self._focus_index = None
            return
        self._focus_index = len(self._entries) - 1
        self._publish_focused()

    def focus_previous(self) -> None:
        if self._focus_index is None:
            self.focus_latest()
            return
        if self._focus_index <= 0:
            return
        self._focus_index -= 1
        self._publish_focused()

    def focus_next(self) -> None:
        if self._focus_index is None:
            return
        if self._focus_index >= len(self._entries) - 1:
            return
        self._focus_index += 1
        self._publish_focused()

    # ------------------------------------------------------------------
    # Quick-edit sub-mode
    # ------------------------------------------------------------------
    def begin_quick_edit(self) -> Optional[str]:
        """Open the quick-edit sub-mode on the focused entry's binding.

        Returns the field being edited, or None when the entry has no
        associated binding (only AUDIO_SPOKEN entries carry a
        ``binding_id``). The first call on a fresh selection starts on
        ``say_template``; subsequent calls rotate through
        ``QUICK_EDIT_FIELDS`` so the user can reach every field from
        the keyboard without a settings detour.
        """
        entry = self.selected_entry()
        if entry is None or entry.binding_id is None:
            return None
        binding = self._find_binding(entry.binding_id)
        if binding is None:
            return None
        if self._quick_edit_binding_id != entry.binding_id:
            self._quick_edit_field = QUICK_EDIT_FIELDS[0]
            self._quick_edit_binding_id = entry.binding_id
        else:
            # Rotate to the next field.
            current = self._quick_edit_field or QUICK_EDIT_FIELDS[0]
            next_index = (QUICK_EDIT_FIELDS.index(current) + 1) % len(
                QUICK_EDIT_FIELDS
            )
            self._quick_edit_field = QUICK_EDIT_FIELDS[next_index]
        self._quick_edit_buffer = _field_value_as_string(
            binding, self._quick_edit_field
        )
        return self._quick_edit_field

    def extend_quick_edit(self, character: str) -> None:
        if self._quick_edit_field is None or not character:
            return
        self._quick_edit_buffer += character

    def backspace_quick_edit(self) -> None:
        if self._quick_edit_field is None:
            return
        self._quick_edit_buffer = self._quick_edit_buffer[:-1]

    def cancel_quick_edit(self) -> None:
        self._cancel_quick_edit_state()

    def commit_quick_edit(self) -> Optional[EventBinding]:
        """Apply the buffer to the focused entry's binding.

        Returns the updated binding on success, or None when no
        binding was under the cursor. Raises ``EventLogError`` on a
        validation failure (e.g. ``volume`` not a number, ``enabled``
        not yes/no/true/false) so the caller can narrate the problem
        without crashing the viewer.
        """
        if self._quick_edit_field is None or self._quick_edit_binding_id is None:
            return None
        field_name = self._quick_edit_field
        binding = self._find_binding(self._quick_edit_binding_id)
        if binding is None:
            self._cancel_quick_edit_state()
            return None
        try:
            new_value = _coerce_field_value(field_name, self._quick_edit_buffer)
        except ValueError as exc:
            raise EventLogError(str(exc)) from exc
        updated = _with_field(binding, field_name, new_value)
        new_bindings = [
            updated if b.id == binding.id else b
            for b in self._sound_engine.bank.bindings
        ]
        try:
            new_bank = self._sound_engine.bank.with_replaced(
                bindings=new_bindings
            )
            self._sound_engine.set_bank(new_bank)
        except SoundBankError as exc:
            raise EventLogError(str(exc)) from exc
        publish_event(
            self._bus,
            EventType.EVENT_LOG_QUICK_EDIT_COMMITTED,
            {
                "binding_id": binding.id,
                "field": field_name,
                "value": new_value,
            },
            source=self.SOURCE,
        )
        self._cancel_quick_edit_state()
        return updated

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------
    def replay_selected(self) -> Optional[EventLogEntry]:
        """Re-publish the focused entry so its binding speaks again.

        Returns the replayed entry, or None when nothing is focused.
        The synthesized event carries a ``replay=True`` marker so
        handlers that want to suppress cascades (file logger,
        bookkeepers) can tell the original apart from the re-run.
        """
        entry = self.selected_entry()
        if entry is None:
            return None
        try:
            event_type = EventType(entry.event_type)
        except ValueError:
            return None
        payload = dict(entry.payload)
        payload["replay"] = True
        publish_event(self._bus, event_type, payload, source=self.SOURCE)
        publish_event(
            self._bus,
            EventType.EVENT_LOG_REPLAYED,
            {
                "event_type": entry.event_type,
                "binding_id": entry.binding_id,
                "narration": entry.narration,
            },
            source=self.SOURCE,
        )
        return entry

    # ------------------------------------------------------------------
    # Wildcard subscriber
    # ------------------------------------------------------------------
    def _on_event(self, event: Event) -> None:
        if event.source == self.SOURCE:
            # Don't re-record the viewer's own open/close/focus
            # announcements — that would bury the actual tail.
            return
        if event.event_type in {
            EventType.EVENT_LOG_OPENED,
            EventType.EVENT_LOG_CLOSED,
            EventType.EVENT_LOG_FOCUSED,
            EventType.EVENT_LOG_QUICK_EDIT_COMMITTED,
            EventType.EVENT_LOG_REPLAYED,
        }:
            return
        entry = EventLogEntry.from_event(event)
        at_tail = (
            self._focus_index is None
            or self._focus_index == len(self._entries) - 1
        )
        self._entries.append(entry)
        if at_tail:
            self._focus_index = len(self._entries) - 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _summary_line(self) -> str:
        entry = self.selected_entry()
        if entry is None:
            return f"event log, {len(self._entries)} entries"
        return (
            f"event log, {len(self._entries)} entries, latest: "
            f"{entry.narration}"
        )

    def _publish_focused(self) -> None:
        entry = self.selected_entry()
        publish_event(
            self._bus,
            EventType.EVENT_LOG_FOCUSED,
            {
                "index": self._focus_index,
                "total": len(self._entries),
                "narration": entry.narration if entry is not None else "",
                "event_type": entry.event_type if entry is not None else "",
                "binding_id": entry.binding_id if entry is not None else None,
            },
            source=self.SOURCE,
        )

    def _find_binding(self, binding_id: str) -> Optional[EventBinding]:
        for binding in self._sound_engine.bank.bindings:
            if binding.id == binding_id:
                return binding
        return None

    def _cancel_quick_edit_state(self) -> None:
        self._quick_edit_field = None
        self._quick_edit_buffer = ""
        self._quick_edit_binding_id = None


def _field_value_as_string(binding: EventBinding, field_name: str) -> str:
    if field_name == "say_template":
        return binding.say_template or ""
    if field_name == "voice_id":
        return binding.voice_id or ""
    if field_name == "enabled":
        return "true" if binding.enabled else "false"
    if field_name == "priority":
        return str(binding.priority)
    raise EventLogError(f"unknown quick-edit field: {field_name}")


def _coerce_field_value(field_name: str, raw: str) -> Any:
    if field_name in {"say_template", "voice_id"}:
        return raw
    if field_name == "enabled":
        lowered = raw.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
        raise ValueError(
            f"`enabled` must be true/false (got {raw!r})"
        )
    if field_name == "priority":
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"`priority` must be an integer (got {raw!r})"
            ) from exc
        return value
    raise ValueError(f"unknown field {field_name!r}")


def _with_field(
    binding: EventBinding, field_name: str, value: Any
) -> EventBinding:
    """Return a copy of ``binding`` with ``field_name`` set to ``value``."""
    from dataclasses import replace

    if field_name == "say_template":
        return replace(binding, say_template=value)
    if field_name == "voice_id":
        return replace(binding, voice_id=value)
    if field_name == "enabled":
        return replace(binding, enabled=bool(value))
    if field_name == "priority":
        return replace(binding, priority=int(value))
    raise EventLogError(f"unknown quick-edit field: {field_name}")
