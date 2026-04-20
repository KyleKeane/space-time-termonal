"""EventLogFile: grouped, human-readable text log of the event stream (F63).

The JSONL logger (``JsonlEventLogger``) is faithful but noisy — one
object per event, in order, every field serialized. For a user who
wants to skim "what happened in the last few keystrokes?" the
grouped text log is friendlier: each ``KEY_PRESSED`` event starts a
new paragraph, and every follow-on event is indented under it until
the next keystroke. ``tail -f`` reads as a conversation.

File layout:

    2026-04-20T12:34:56 KEY_PRESSED name=ctrl_e char=
      2026-04-20T12:34:56 focus.changed new_mode=event_log
      2026-04-20T12:34:56 event_log.opened count=3
    2026-04-20T12:34:57 KEY_PRESSED name=up char=
      2026-04-20T12:34:57 event_log.focused index=2 narration='audio spoken, ...'

The header path is ``events-YYYY-MM-DD.log`` under the target
directory so a long-running session rolls over at midnight; each
launch appends (so relaunching in the same day keeps history).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TextIO

from asat.event_bus import WILDCARD, EventBus
from asat.events import Event, EventType


class EventLogFile:
    """Wildcard subscriber that writes the event stream as grouped text."""

    SOURCE = "event_log_file"

    def __init__(self, bus: EventBus, directory: Path | str) -> None:
        """Open today's log under ``directory`` and subscribe."""
        self._bus = bus
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._stream: TextIO | None = None
        self._closed = False
        self._wrote_group_header = False
        bus.subscribe(WILDCARD, self._on_event)

    @property
    def directory(self) -> Path:
        return self._directory

    def current_path(self) -> Path:
        """Return the path the logger is currently writing to."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._directory / f"events-{today}.log"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus.unsubscribe(WILDCARD, self._on_event)
        if self._stream is not None:
            try:
                self._stream.flush()
            finally:
                self._stream.close()
        self._stream = None

    def _on_event(self, event: Event) -> None:
        if self._closed:
            return
        stream = self._stream_for(event.timestamp)
        line = _format_line(event)
        if event.event_type is EventType.KEY_PRESSED:
            # Start a fresh group; subsequent events indent under it.
            if self._wrote_group_header:
                # Separate groups with a blank line so `less` renders
                # them as distinct paragraphs.
                stream.write("\n")
            stream.write(line + "\n")
            self._wrote_group_header = True
            return
        indent = "  " if self._wrote_group_header else ""
        stream.write(f"{indent}{line}\n")

    def _stream_for(self, timestamp: datetime) -> TextIO:
        """Open / rotate the per-day log file based on ``timestamp``."""
        day = timestamp.strftime("%Y-%m-%d")
        if day != self._current_date or self._stream is None:
            if self._stream is not None:
                self._stream.close()
            path = self._directory / f"events-{day}.log"
            self._stream = path.open("a", encoding="utf-8", buffering=1)
            self._current_date = day
            # Force a fresh group header on the next KEY_PRESSED so a
            # day rollover always starts on a clean paragraph.
            self._wrote_group_header = False
        return self._stream


def _format_line(event: Event) -> str:
    """Render one event as a single log-line string.

    Payloads are collapsed to ``key=repr(value)`` pairs in insertion
    order so the text is greppable without being JSON. Long string
    values are ``repr``'d (i.e. quoted) so surrounding whitespace is
    visible.
    """
    stamp = event.timestamp.strftime("%Y-%m-%dT%H:%M:%S")
    if event.event_type is EventType.KEY_PRESSED:
        name = event.payload.get("name", "?")
        char = event.payload.get("char", "")
        return f"{stamp} KEY_PRESSED name={name} char={char!r}"
    payload_bits = " ".join(
        f"{key}={_format_value(value)}"
        for key, value in event.payload.items()
    )
    suffix = f" {payload_bits}" if payload_bits else ""
    return f"{stamp} {event.event_type.value}{suffix}"


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return repr(value)
    return str(value)
