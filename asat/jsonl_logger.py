"""JsonlEventLogger: persist every event on the bus to a JSON-lines file.

A wildcard subscriber that writes one JSON object per line for every
``Event`` the bus delivers. The format is intentionally dumb so a
human, a screen reader, or a later replay harness can parse it
without importing anything ASAT-specific.

The file is opened in ``"w"`` mode so each ASAT session starts with
a clean log. A long-running install therefore never grows an
unbounded history; a user who wants archival logs can add their own
``--log session-%Y%m%d.jsonl`` rotation via the shell. Line buffering
is enabled so a partially-run session still leaves a usable tail on
disk if the process is killed.

Payload values that are not JSON-serialisable fall back to ``repr`` —
a weird payload should never crash the logger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from asat.event_bus import WILDCARD, EventBus
from asat.events import Event


class JsonlEventLogger:
    """Wildcard bus subscriber that writes every Event as one JSON line."""

    SOURCE = "jsonl_logger"

    def __init__(self, bus: EventBus, path: Path | str) -> None:
        """Open ``path`` for writing and subscribe to the wildcard channel."""
        self._bus = bus
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._stream: TextIO = self._path.open(
            "w", encoding="utf-8", buffering=1
        )
        self._closed = False
        bus.subscribe(WILDCARD, self._on_event)

    @property
    def path(self) -> Path:
        """Return the file the logger is writing to."""
        return self._path

    def close(self) -> None:
        """Unsubscribe and flush. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self._bus.unsubscribe(WILDCARD, self._on_event)
        try:
            self._stream.flush()
        finally:
            self._stream.close()

    def _on_event(self, event: Event) -> None:
        """Serialise one Event as a JSON object and append it to the file."""
        if self._closed:
            return
        record = {
            "event_type": event.event_type.value,
            "source": event.source,
            "timestamp": event.timestamp.isoformat(),
            "payload": event.payload,
        }
        self._stream.write(json.dumps(record, default=repr))
        self._stream.write("\n")
