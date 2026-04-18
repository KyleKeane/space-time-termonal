"""OutputBuffer: per-cell storage of streamed stdout and stderr lines.

The execution kernel publishes OUTPUT_CHUNK and ERROR_CHUNK events as a
subprocess produces each line. Those events are fine for real-time
audio, but a blind user also needs to revisit past output: walk
line-by-line, re-read a specific line, copy one out, or page through a
long result. That demands a structured per-cell line store, which is
what OutputBuffer provides.

The module is split into three pieces:

OutputLine is the immutable record of a single captured line.
OutputBuffer stores the lines for one cell and exposes random access,
    slicing, paging, and filtering by stream. It is intentionally a
    passive container: it does not subscribe to the event bus itself.
OutputRecorder subscribes to OUTPUT_CHUNK/ERROR_CHUNK events and
    funnels them into per-cell OutputBuffer instances, creating a new
    buffer the first time a cell produces output. It also publishes
    OUTPUT_LINE_APPENDED events so downstream consumers (the output
    cursor, a line-level audio previewer) can react without re-parsing
    the raw chunks.

All classes are stdlib-only and synchronous, matching the rest of the
project.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from asat.event_bus import EventBus
from asat.events import Event, EventType


STDOUT = "stdout"
STDERR = "stderr"


@dataclass(frozen=True)
class OutputLine:
    """A single captured line of output.

    cell_id: the cell that produced this line.
    line_number: zero-based index within the owning buffer, assigned
        when the line was appended.
    stream: "stdout" or "stderr".
    text: the captured line content with its trailing newline stripped
        by the runner before it reached us.
    """

    cell_id: str
    line_number: int
    stream: str
    text: str


class OutputBuffer:
    """Per-cell ordered store of captured output lines."""

    def __init__(self, cell_id: str) -> None:
        """Create an empty buffer bound to a single cell id."""
        self._cell_id = cell_id
        self._lines: list[OutputLine] = []

    @property
    def cell_id(self) -> str:
        """Return the id of the cell this buffer belongs to."""
        return self._cell_id

    def __len__(self) -> int:
        """Return the number of captured lines."""
        return len(self._lines)

    def __iter__(self) -> Iterator[OutputLine]:
        """Iterate over captured lines in insertion order."""
        return iter(self._lines)

    def append(self, text: str, stream: str = STDOUT) -> OutputLine:
        """Append a single line and return the newly created OutputLine.

        The caller is responsible for having already split multi-line
        chunks. This matches how the kernel emits events (one line per
        chunk), and keeps the buffer simple.
        """
        if stream not in (STDOUT, STDERR):
            raise ValueError(f"Unknown output stream: {stream!r}")
        line = OutputLine(
            cell_id=self._cell_id,
            line_number=len(self._lines),
            stream=stream,
            text=text,
        )
        self._lines.append(line)
        return line

    def lines(self) -> tuple[OutputLine, ...]:
        """Return all captured lines as an immutable tuple."""
        return tuple(self._lines)

    def line(self, line_number: int) -> OutputLine:
        """Return the line at the given zero-based index."""
        if line_number < 0 or line_number >= len(self._lines):
            raise IndexError(f"line_number {line_number} is out of range")
        return self._lines[line_number]

    def page(self, start: int, size: int) -> tuple[OutputLine, ...]:
        """Return up to size consecutive lines starting at start.

        Out-of-range starts clamp to the nearest end. The returned slice
        may be shorter than size if the buffer ends first.
        """
        if size <= 0:
            raise ValueError("page size must be positive")
        clamped_start = max(0, min(start, len(self._lines)))
        return tuple(self._lines[clamped_start:clamped_start + size])

    def lines_on_stream(self, stream: str) -> tuple[OutputLine, ...]:
        """Return only the lines captured from the given stream."""
        if stream not in (STDOUT, STDERR):
            raise ValueError(f"Unknown output stream: {stream!r}")
        return tuple(line for line in self._lines if line.stream == stream)

    def clear(self) -> None:
        """Drop every captured line. Useful when a cell is re-executed."""
        self._lines.clear()


class OutputRecorder:
    """Subscribes to output events and fills per-cell OutputBuffers.

    On every OUTPUT_CHUNK or ERROR_CHUNK event the recorder looks up
    (or creates) the buffer for the owning cell, appends the line, and
    publishes a richer OUTPUT_LINE_APPENDED event containing the new
    line's position. Downstream consumers (cursor, audio previewer)
    can subscribe once to OUTPUT_LINE_APPENDED rather than having to
    reconstruct line numbers themselves.
    """

    SOURCE = "output_recorder"

    def __init__(self, bus: EventBus) -> None:
        """Attach the recorder to a bus and subscribe to stream events."""
        self._bus = bus
        self._buffers: dict[str, OutputBuffer] = {}
        bus.subscribe(EventType.OUTPUT_CHUNK, self._on_stdout_chunk)
        bus.subscribe(EventType.ERROR_CHUNK, self._on_stderr_chunk)

    def buffer_for(self, cell_id: str) -> OutputBuffer:
        """Return the buffer for a cell, creating it on first access."""
        buffer = self._buffers.get(cell_id)
        if buffer is None:
            buffer = OutputBuffer(cell_id=cell_id)
            self._buffers[cell_id] = buffer
        return buffer

    def has_buffer_for(self, cell_id: str) -> bool:
        """Return True if a buffer has ever been created for this cell."""
        return cell_id in self._buffers

    def discard(self, cell_id: str) -> Optional[OutputBuffer]:
        """Forget the buffer for the given cell. Returns it, or None."""
        return self._buffers.pop(cell_id, None)

    def _on_stdout_chunk(self, event: Event) -> None:
        """Route a stdout chunk into the appropriate buffer."""
        self._record(event, STDOUT)

    def _on_stderr_chunk(self, event: Event) -> None:
        """Route a stderr chunk into the appropriate buffer."""
        self._record(event, STDERR)

    def _record(self, event: Event, stream: str) -> None:
        """Append one chunk line and republish an enriched event."""
        cell_id = event.payload.get("cell_id")
        text = event.payload.get("line")
        if not isinstance(cell_id, str) or not isinstance(text, str):
            return
        line = self.buffer_for(cell_id).append(text, stream=stream)
        self._bus.publish(
            Event(
                event_type=EventType.OUTPUT_LINE_APPENDED,
                payload={
                    "cell_id": cell_id,
                    "line_number": line.line_number,
                    "stream": stream,
                    "text": text,
                },
                source=self.SOURCE,
            )
        )
