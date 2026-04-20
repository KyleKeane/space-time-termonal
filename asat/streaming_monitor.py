"""StreamingMonitor: silence detection + periodic beats for long output (F37).

A long-running command (``pytest``, ``make``, log tailing) emits
hundreds of ``OUTPUT_CHUNK`` events. After thirty seconds of streaming
a user has lost sense of progress, and a command that hangs silently
is indistinguishable from one that is just slow. The StreamingMonitor
closes both gaps by subscribing to the execution-event stream and
republishing two synthetic events:

- ``OUTPUT_STREAM_PAUSED`` fires the first time the gap between the
  last chunk and "now" crosses ``silence_threshold_sec`` while a cell
  is still running. The default bank binds it to a quiet cue the user
  hears once — "the stream went quiet".
- ``OUTPUT_STREAM_BEAT`` fires every ``progress_beat_interval_sec``
  while streaming. The default bank binds it to a subtle progress
  tick so the user keeps a temporal frame on a noisy build.

The monitor itself is pure: ``check()`` consults the injected clock
and publishes at most one event per call. Production wiring launches
a small daemon thread via ``start_background_ticker()`` that invokes
``check()`` every ``tick_interval_sec``; tests drive ``check`` directly
with an injected clock so no thread or real time is needed.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType


class StreamingMonitor:
    """Track per-cell output streaming and publish pacing events."""

    SOURCE = "streaming_monitor"

    def __init__(
        self,
        bus: EventBus,
        *,
        silence_threshold_sec: float = 5.0,
        progress_beat_interval_sec: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Subscribe to the streaming events on `bus`.

        ``silence_threshold_sec`` controls how quiet the chunk stream
        must be before an ``OUTPUT_STREAM_PAUSED`` event fires;
        ``progress_beat_interval_sec`` controls how often an
        ``OUTPUT_STREAM_BEAT`` fires while streaming is live. Both
        accept fractional seconds so tests can run on a compressed
        timeline. ``clock`` is injected so tests advance time
        deterministically without real sleeps.
        """
        if silence_threshold_sec <= 0:
            raise ValueError("silence_threshold_sec must be > 0")
        if progress_beat_interval_sec <= 0:
            raise ValueError("progress_beat_interval_sec must be > 0")
        self._bus = bus
        self._silence_threshold = float(silence_threshold_sec)
        self._beat_interval = float(progress_beat_interval_sec)
        self._clock = clock
        self._active_cell_id: Optional[str] = None
        self._started_at: Optional[float] = None
        self._last_chunk_at: Optional[float] = None
        self._last_beat_at: Optional[float] = None
        self._paused_sent = False
        self._ticker_thread: Optional[threading.Thread] = None
        self._ticker_stop: Optional[threading.Event] = None
        for event_type in (
            EventType.COMMAND_STARTED,
            EventType.OUTPUT_CHUNK,
            EventType.ERROR_CHUNK,
            EventType.COMMAND_COMPLETED,
            EventType.COMMAND_FAILED,
            EventType.COMMAND_CANCELLED,
        ):
            bus.subscribe(event_type, self._on_event)

    @property
    def active_cell_id(self) -> Optional[str]:
        """Return the cell currently being tracked, or None if idle."""
        return self._active_cell_id

    def _on_event(self, event: Event) -> None:
        """Update internal state from an execution-lifecycle event."""
        now = self._clock()
        cell_id = event.payload.get("cell_id")
        if event.event_type is EventType.COMMAND_STARTED:
            self._start_tracking(str(cell_id) if cell_id is not None else None, now)
        elif event.event_type in (EventType.OUTPUT_CHUNK, EventType.ERROR_CHUNK):
            self._record_chunk(cell_id, now)
        else:
            self._stop_tracking(cell_id)

    def _start_tracking(self, cell_id: Optional[str], now: float) -> None:
        """Begin silence/beat tracking for a freshly-launched cell."""
        self._active_cell_id = cell_id
        self._started_at = now
        self._last_chunk_at = now
        self._last_beat_at = now
        self._paused_sent = False

    def _record_chunk(self, cell_id: object, now: float) -> None:
        """Reset the silence timer when a chunk arrives for the active cell."""
        if self._active_cell_id is None or cell_id != self._active_cell_id:
            return
        self._last_chunk_at = now
        # A fresh chunk after a pause re-arms the gate so a subsequent
        # silence window fires again. Pause is a "once per quiet
        # stretch" signal, not a level.
        self._paused_sent = False

    def _stop_tracking(self, cell_id: object) -> None:
        """Clear state when the active cell's command ends."""
        if self._active_cell_id is None or cell_id != self._active_cell_id:
            return
        self._active_cell_id = None
        self._started_at = None
        self._last_chunk_at = None
        self._last_beat_at = None
        self._paused_sent = False

    def check(self, now: Optional[float] = None) -> None:
        """Publish any pacing events whose thresholds have elapsed.

        Idempotent when no active cell is being tracked; safe to call
        every tick of a background poller. ``now`` lets tests step the
        virtual clock without touching the real one.
        """
        if self._active_cell_id is None:
            return
        current = self._clock() if now is None else now
        if (
            not self._paused_sent
            and self._last_chunk_at is not None
            and current - self._last_chunk_at >= self._silence_threshold
        ):
            gap = current - self._last_chunk_at
            publish_event(
                self._bus,
                EventType.OUTPUT_STREAM_PAUSED,
                {"cell_id": self._active_cell_id, "gap_sec": gap},
                source=self.SOURCE,
            )
            self._paused_sent = True
        if (
            self._last_beat_at is not None
            and self._started_at is not None
            and current - self._last_beat_at >= self._beat_interval
        ):
            elapsed = current - self._started_at
            publish_event(
                self._bus,
                EventType.OUTPUT_STREAM_BEAT,
                {"cell_id": self._active_cell_id, "elapsed_sec": elapsed},
                source=self.SOURCE,
            )
            self._last_beat_at = current

    def start_background_ticker(self, tick_interval_sec: float = 1.0) -> None:
        """Launch a daemon thread that polls ``check()`` in production.

        Tests don't call this; they invoke ``check()`` with an injected
        clock. ``tick_interval_sec`` should divide both thresholds so
        no cue fires noticeably late.
        """
        if self._ticker_thread is not None:
            return
        stop_event = threading.Event()

        def _tick() -> None:
            while not stop_event.wait(tick_interval_sec):
                try:
                    self.check()
                except Exception:
                    # A misbehaving subscriber shouldn't take down the
                    # ticker; the next iteration retries.
                    continue

        thread = threading.Thread(
            target=_tick, name="asat-streaming-monitor", daemon=True
        )
        self._ticker_stop = stop_event
        self._ticker_thread = thread
        thread.start()

    def close(self) -> None:
        """Stop the background ticker if one was started."""
        if self._ticker_stop is not None:
            self._ticker_stop.set()
        self._ticker_thread = None
        self._ticker_stop = None
