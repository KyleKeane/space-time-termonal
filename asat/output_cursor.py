"""OutputCursor: line-level navigation through a cell's captured output.

When the user flips a cell into OUTPUT focus mode, keystrokes should
walk through the cell's output one line at a time. The OutputCursor is
the small state machine that tracks "which line is currently under the
ear" and exposes the motions the input router binds to.

The cursor holds a reference to an OutputBuffer, an integer line index,
and a page size. It is deliberately dumb about audio: its only job is
to move the selection and publish OUTPUT_LINE_FOCUSED events. The audio
engine (or any other observer) can subscribe to those events and
voice the focused line.

A cursor can be attached and re-attached as focus moves between cells.
attach() snaps to the last line of the target buffer, which is the
most useful position for a freshly finished command. Callers can then
call move_to_start() to jump to the top of the output.
"""

from __future__ import annotations

from typing import Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.output_buffer import OutputBuffer, OutputLine


DEFAULT_PAGE_SIZE = 10


class OutputCursor:
    """Stateful cursor over the lines of a single OutputBuffer."""

    SOURCE = "output_cursor"

    def __init__(
        self,
        bus: EventBus,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        """Create an unattached cursor bound to an event bus.

        The cursor starts detached (no buffer). Call attach() to bind it
        to a specific cell's buffer before issuing navigation commands.
        """
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self._bus = bus
        self._page_size = page_size
        self._buffer: Optional[OutputBuffer] = None
        self._index: int = -1

    @property
    def page_size(self) -> int:
        """Return the number of lines a page motion traverses."""
        return self._page_size

    @property
    def buffer(self) -> Optional[OutputBuffer]:
        """Return the currently attached buffer, or None if detached."""
        return self._buffer

    @property
    def line_number(self) -> Optional[int]:
        """Return the zero-based index of the focused line, or None."""
        if self._buffer is None or self._index < 0:
            return None
        return self._index

    def attach(self, buffer: OutputBuffer) -> Optional[OutputLine]:
        """Bind the cursor to a buffer, snapping to its last line.

        Returns the newly focused line, or None if the buffer is empty.
        """
        self._buffer = buffer
        if len(buffer) == 0:
            self._index = -1
            return None
        self._index = len(buffer) - 1
        line = buffer.line(self._index)
        self._publish_focus(line)
        return line

    def detach(self) -> None:
        """Drop the attached buffer without publishing any event."""
        self._buffer = None
        self._index = -1

    def current_line(self) -> Optional[OutputLine]:
        """Return the currently focused OutputLine, or None if empty."""
        if self._buffer is None or self._index < 0:
            return None
        if self._index >= len(self._buffer):
            return None
        return self._buffer.line(self._index)

    def move_line_up(self) -> Optional[OutputLine]:
        """Focus the previous line; no-op at the top."""
        return self._seek(self._index - 1)

    def move_line_down(self) -> Optional[OutputLine]:
        """Focus the next line; no-op at the bottom."""
        return self._seek(self._index + 1)

    def move_page_up(self) -> Optional[OutputLine]:
        """Jump up by a page; clamps at the first line."""
        return self._seek(self._index - self._page_size)

    def move_page_down(self) -> Optional[OutputLine]:
        """Jump down by a page; clamps at the last line."""
        return self._seek(self._index + self._page_size)

    def move_to_start(self) -> Optional[OutputLine]:
        """Focus the first line of the buffer."""
        if self._buffer is None or len(self._buffer) == 0:
            return None
        return self._seek(0)

    def move_to_end(self) -> Optional[OutputLine]:
        """Focus the last line of the buffer."""
        if self._buffer is None or len(self._buffer) == 0:
            return None
        return self._seek(len(self._buffer) - 1)

    def _seek(self, target: int) -> Optional[OutputLine]:
        """Clamp target into range, move, and publish if actually changed."""
        if self._buffer is None or len(self._buffer) == 0:
            return None
        clamped = max(0, min(target, len(self._buffer) - 1))
        if clamped == self._index:
            return self._buffer.line(self._index)
        self._index = clamped
        line = self._buffer.line(self._index)
        self._publish_focus(line)
        return line

    def _publish_focus(self, line: OutputLine) -> None:
        """Publish an OUTPUT_LINE_FOCUSED event for the given line."""
        publish_event(
            self._bus,
            EventType.OUTPUT_LINE_FOCUSED,
            {
                "cell_id": line.cell_id,
                "line_number": line.line_number,
                "stream": line.stream,
                "text": line.text,
            },
            source=self.SOURCE,
        )
