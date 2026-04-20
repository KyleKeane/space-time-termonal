"""OutputPlaybackDriver: auto-advance the OutputCursor through a buffer (F24).

When the user taps ``p`` or ``Space`` in OUTPUT mode, ASAT should
play the captured output end-to-end instead of making them step
line-by-line with Up / Down. The driver owns a small state machine
plus a background ticker thread that calls
``output_cursor.move_line_down()`` at a fixed interval. Every step
publishes `OUTPUT_LINE_FOCUSED` (via the cursor) so the existing
narration pipeline narrates each line without special casing.

The driver is intentionally passive about *how* playback ends — the
Application is the authoritative owner of that decision. Any key
press while active stops playback (`reason="cancelled"`); leaving
OUTPUT mode stops it (`reason="focus_changed"`); reaching the end of
the buffer stops it (`reason="end"`). Tests can drive the ticker
manually via ``step()`` with an injected ``clock`` to avoid
real-time sleeps.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.output_cursor import OutputCursor


DEFAULT_INTERVAL_SEC = 1.5


class OutputPlaybackDriver:
    """Step an `OutputCursor` through its buffer on a fixed cadence."""

    SOURCE = "output_playback"

    def __init__(
        self,
        bus: EventBus,
        cursor: OutputCursor,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_sec <= 0.0:
            raise ValueError("interval_sec must be positive")
        self._bus = bus
        self._cursor = cursor
        self._interval_sec = interval_sec
        self._clock = clock
        self._active = False
        self._cell_id: Optional[str] = None
        self._next_tick_at: float = 0.0
        self._ticker: Optional[threading.Thread] = None
        self._stop_signal = threading.Event()

    @property
    def active(self) -> bool:
        """Return True while the driver is auto-advancing."""
        return self._active

    @property
    def interval_sec(self) -> float:
        return self._interval_sec

    def start(self, cell_id: Optional[str] = None) -> bool:
        """Begin auto-advancing. Returns False when already active or at end.

        ``cell_id`` is forwarded to `OUTPUT_PLAYBACK_STARTED` /
        `OUTPUT_PLAYBACK_STOPPED` so a binding can reference which cell
        was being played. Returns False when no line can advance — the
        cursor is detached, the buffer is empty, or the cursor is
        already sitting on the last line — so the Application can emit
        a hint instead of pretending playback is running.
        """
        if self._active:
            return False
        if self._cursor.buffer is None:
            return False
        if not self._has_more_lines():
            return False
        self._active = True
        self._cell_id = cell_id
        self._next_tick_at = self._clock() + self._interval_sec
        publish_event(
            self._bus,
            EventType.OUTPUT_PLAYBACK_STARTED,
            {
                "cell_id": cell_id,
                "interval_sec": self._interval_sec,
            },
            source=self.SOURCE,
        )
        return True

    def stop(self, reason: str = "cancelled") -> bool:
        """Halt playback and publish OUTPUT_PLAYBACK_STOPPED.

        Returns True iff the driver was active; a no-op otherwise so
        "stop on any key" paths can be safely called unconditionally.
        """
        if not self._active:
            return False
        cell_id = self._cell_id
        self._active = False
        self._cell_id = None
        self._stop_signal.set()
        publish_event(
            self._bus,
            EventType.OUTPUT_PLAYBACK_STOPPED,
            {"cell_id": cell_id, "reason": reason},
            source=self.SOURCE,
        )
        return True

    def step(self, now: Optional[float] = None) -> bool:
        """Advance one line when the next tick is due. Returns True on step.

        The production ticker thread calls this each poll; tests call
        it directly with an explicit ``now`` to drive deterministic
        scenarios.
        """
        if not self._active:
            return False
        current = now if now is not None else self._clock()
        if current < self._next_tick_at:
            return False
        line = self._cursor.move_line_down()
        if line is None or not self._has_more_lines():
            # `line is None` means we were already at the end when the
            # tick arrived; `not has_more` means the move_line_down
            # call just landed us on the last line. Either way, end.
            self.stop(reason="end")
            return line is not None
        self._next_tick_at = current + self._interval_sec
        return True

    def start_background_ticker(self, poll_interval_sec: float = 0.05) -> None:
        """Launch a daemon thread that calls `step()` on a poll interval.

        Safe to call once per driver instance; subsequent calls are
        ignored. Daemon thread lifetime is tied to the interpreter,
        and `stop()` clears the internal ``_stop_signal`` so the loop
        exits promptly on shutdown.
        """
        if self._ticker is not None:
            return
        self._stop_signal.clear()

        def _run() -> None:
            while not self._stop_signal.is_set():
                if self._active:
                    self.step()
                self._stop_signal.wait(poll_interval_sec)

        thread = threading.Thread(
            target=_run, name="asat-output-playback", daemon=True
        )
        self._ticker = thread
        thread.start()

    def close(self) -> None:
        """Stop playback (if active) and signal the background thread."""
        self.stop(reason="cancelled")
        self._stop_signal.set()

    def _has_more_lines(self) -> bool:
        buffer = self._cursor.buffer
        if buffer is None or len(buffer) == 0:
            return False
        current = self._cursor.line_number
        if current is None:
            return False
        return current < len(buffer) - 1
