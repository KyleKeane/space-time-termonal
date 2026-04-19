"""StderrTailAnnouncer: auto-read the last stderr lines on failure.

A blind user hearing only "failed with exit code 1" has to navigate
into OUTPUT mode and scroll back to find the actual error text. The
single most useful piece of information — what went wrong — sits one
navigation step away. This module closes that gap.

`StderrTailAnnouncer` subscribes to `COMMAND_FAILED`, fetches the
failed cell's stderr tail from the `OutputRecorder`, and republishes
a richer `COMMAND_FAILED_STDERR_TAIL` event whose payload carries
the tail text, ready for the default bank to narrate through the
`alert` voice.

Architecture: the feature lives in its own event-driven subscriber
rather than extending `SoundEngine` directly. That keeps the audio
layer agnostic to cell state and lets users silence the narration
with a single `enabled = false` toggle on the default binding — no
code change required.

See `docs/FEATURE_REQUESTS.md#f36` for the user-facing rationale and
`docs/EVENTS.md#execution-kernel` for the event payload reference.
"""

from __future__ import annotations

from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.output_buffer import OutputRecorder, STDERR


DEFAULT_TAIL_LINES = 3


class StderrTailAnnouncer:
    """Publish `COMMAND_FAILED_STDERR_TAIL` with the last N stderr lines."""

    SOURCE = "error_tail"

    def __init__(
        self,
        bus: EventBus,
        recorder: OutputRecorder,
        *,
        tail_lines: int = DEFAULT_TAIL_LINES,
    ) -> None:
        """Subscribe to COMMAND_FAILED and remember how many lines to quote.

        `tail_lines` must be a positive integer; a value of 3 is the
        default because it usually captures a traceback's final frame
        or a shell error without drowning the user. Out-of-range
        values surface at construction rather than silently passing
        through.
        """
        if tail_lines < 1:
            raise ValueError(
                f"tail_lines must be >= 1, got {tail_lines}"
            )
        self._bus = bus
        self._recorder = recorder
        self._tail_lines = tail_lines
        bus.subscribe(EventType.COMMAND_FAILED, self._on_command_failed)

    @property
    def tail_lines(self) -> int:
        """Return the maximum number of stderr lines announced per failure."""
        return self._tail_lines

    def _on_command_failed(self, event: Event) -> None:
        """Read stderr tail from the recorder and republish, if any."""
        payload = event.payload
        cell_id = payload.get("cell_id")
        if not isinstance(cell_id, str):
            return
        if not self._recorder.has_buffer_for(cell_id):
            return
        stderr_lines = self._recorder.buffer_for(cell_id).lines_on_stream(STDERR)
        if not stderr_lines:
            return
        tail = stderr_lines[-self._tail_lines:]
        tail_texts = [line.text for line in tail]
        publish_event(
            self._bus,
            EventType.COMMAND_FAILED_STDERR_TAIL,
            {
                "cell_id": cell_id,
                "exit_code": payload.get("exit_code"),
                "timed_out": bool(payload.get("timed_out", False)),
                "tail_lines": tail_texts,
                "tail_text": "\n".join(tail_texts),
                "line_count": len(tail_texts),
            },
            source=self.SOURCE,
        )
