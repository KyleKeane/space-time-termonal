"""TerminalRenderer: a minimal text view of the event stream.

The audio pipeline is the primary UI; this renderer exists so sighted
developers can see what ASAT is doing, and so `python -m asat` feels
like a terminal rather than a silent black hole. Every line it writes
is derived from one event on the bus.

What gets printed:

- `SESSION_CREATED`: one-line banner naming the session and pointing
  at `:help`.
- `HELP_REQUESTED`: the cheat-sheet lines carried on the event.
- `FOCUS_CHANGED`: a one-line status like `[input #1]` whenever the
  mode or focused cell changes.
- `ACTION_INVOKED` with action == `insert_character`: the literal
  character (so the user sees their typing, because cbreak disabled
  echo).
- `ACTION_INVOKED` with action == `backspace` in INPUT mode: `\\b \\b`
  to visually erase the last echoed character.
- `OUTPUT_CHUNK`: the captured stdout line.
- `ERROR_CHUNK`: the captured stderr line, prefixed with `! ` so the
  sighted viewer can tell them apart.
- `COMMAND_COMPLETED` / `COMMAND_FAILED`: one-line summary with the
  exit code.
- `SESSION_SAVED`: one-line confirmation.

Optional outline pane: when the CLI passes ``--view outline`` or
``--view both`` (the TTY default), the renderer also re-paints an
indented outline of the notebook cells whenever the structure or
focus changes. The outline is a block of lines rendered by
``asat.outline.render_outline`` and framed between two marker lines
so it is easy to spot in the text trace and easy to erase with ANSI
clear-screen when the host is a real terminal.

The renderer intentionally does NOT render spatial hints or timing;
that's the audio engine's job. The point is "enough text so the user
is not flying blind." A `--quiet` switch in the CLI disables it.
"""

from __future__ import annotations

import sys
from typing import Callable, Sequence, TextIO

from asat.cell import Cell
from asat.event_bus import EventBus
from asat.event_log import EventLogViewer
from asat.events import Event, EventType
from asat.outline import render_outline


OUTLINE_HEADER = "-- outline --"
OUTLINE_FOOTER = "-- /outline --"
OUTLINE_EMPTY_LINE = "  (no cells yet)"
EVENT_LOG_HEADER = "-- event log --"
EVENT_LOG_FOOTER = "-- /event log --"
EVENT_LOG_EMPTY_LINE = "  (no events yet)"
EVENT_LOG_TAIL_LINES = 10
_ANSI_CLEAR_HOME = "\x1b[H\x1b[2J"


class TerminalRenderer:
    """Subscribe to the bus and write a minimal visible trace to a stream."""

    def __init__(
        self,
        bus: EventBus,
        stream: TextIO | None = None,
        *,
        show_trace: bool = True,
        show_outline: bool = False,
        cells_provider: Callable[[], Sequence[Cell]] | None = None,
        outline_width: int = 80,
        event_log_viewer: EventLogViewer | None = None,
    ) -> None:
        """Attach to the bus and remember where to write (defaults to stdout).

        ``show_trace`` flips the event-stream trace lines (the banner,
        typing echo, command output, `[done]` markers). ``show_outline``
        turns on the indented notebook outline; it requires
        ``cells_provider`` — a zero-argument callable returning the
        current ``Session.cells`` list — because the structural events
        (`CELL_CREATED` / `CELL_UPDATED` / `CELL_REMOVED` / `CELL_MOVED`)
        carry only an id + index, not the full cell data.

        When both panes are active and the stream is a real TTY, every
        outline repaint first emits an ANSI clear-home so the view
        stays fixed rather than scrolling off the top. Non-TTY streams
        (pytest StringIO, piped stdout, log files) get append-only
        blocks instead so the output remains greppable.
        """
        self._bus = bus
        self._stream = stream if stream is not None else sys.stdout
        self._at_line_start = True
        self._show_trace = show_trace
        self._show_outline = show_outline
        self._cells_provider = cells_provider
        self._outline_width = outline_width
        self._event_log_viewer = event_log_viewer
        self._focus_cell_id: str | None = None
        if show_outline and cells_provider is None:
            raise ValueError(
                "TerminalRenderer(show_outline=True) requires a cells_provider"
            )
        if show_trace:
            bus.subscribe(EventType.SESSION_CREATED, self._on_session_created)
            bus.subscribe(EventType.SESSION_LOADED, self._on_session_loaded)
            bus.subscribe(EventType.SESSION_SAVED, self._on_session_saved)
            bus.subscribe(EventType.HELP_REQUESTED, self._on_help_requested)
            bus.subscribe(EventType.ACTION_INVOKED, self._on_action_invoked)
            bus.subscribe(EventType.OUTPUT_CHUNK, self._on_output_chunk)
            bus.subscribe(EventType.ERROR_CHUNK, self._on_error_chunk)
            bus.subscribe(
                EventType.COMMAND_COMPLETED, self._on_command_completed
            )
            bus.subscribe(EventType.COMMAND_FAILED, self._on_command_failed)
            bus.subscribe(EventType.PROMPT_REFRESH, self._on_prompt_refresh)
            bus.subscribe(EventType.FIRST_RUN_DETECTED, self._on_first_run)
        # FOCUS_CHANGED is used by the trace path for the `[input #…]`
        # status line AND by the outline path to track which cell gets
        # the `> ` arrow, so always subscribe when either pane is on.
        if show_trace or show_outline:
            bus.subscribe(EventType.FOCUS_CHANGED, self._on_focus_changed)
        if show_outline:
            bus.subscribe(EventType.CELL_CREATED, self._on_cells_changed)
            bus.subscribe(EventType.CELL_UPDATED, self._on_cells_changed)
            bus.subscribe(EventType.CELL_REMOVED, self._on_cells_changed)
            bus.subscribe(EventType.CELL_MOVED, self._on_cells_changed)
        # F39 event-log panel: repaint whenever the viewer opens, moves
        # focus, or closes. Editing / replay events land back through
        # FOCUS_CHANGED / QUICK_EDIT_COMMITTED so a fresh snapshot shows
        # the updated binding without the viewer having to push a dedicated
        # repaint signal.
        if event_log_viewer is not None:
            bus.subscribe(EventType.EVENT_LOG_OPENED, self._on_event_log_changed)
            bus.subscribe(EventType.EVENT_LOG_FOCUSED, self._on_event_log_changed)
            bus.subscribe(EventType.EVENT_LOG_CLOSED, self._on_event_log_changed)
            bus.subscribe(
                EventType.EVENT_LOG_QUICK_EDIT_COMMITTED,
                self._on_event_log_changed,
            )
            bus.subscribe(
                EventType.EVENT_LOG_REPLAYED, self._on_event_log_changed
            )

    def _write_line(self, text: str) -> None:
        """Print a full line, ending any in-flight keystroke echo first."""
        if not self._at_line_start:
            self._stream.write("\n")
        self._stream.write(text)
        self._stream.write("\n")
        self._stream.flush()
        self._at_line_start = True

    def _write_inline(self, text: str) -> None:
        """Echo raw characters without a trailing newline (for typing feedback)."""
        self._stream.write(text)
        self._stream.flush()
        self._at_line_start = False

    def _on_session_created(self, event: Event) -> None:
        session_id = event.payload.get("session_id", "?")
        self._write_line(
            f"[asat] session {session_id} ready. Type :help for the keystroke "
            "cheat sheet, :quit to exit."
        )

    def _on_help_requested(self, event: Event) -> None:
        lines = event.payload.get("lines", [])
        for line in lines:
            self._write_line(str(line))

    def _on_session_loaded(self, event: Event) -> None:
        path = event.payload.get("path", "?")
        self._write_line(f"[asat] loaded session from {path}.")

    def _on_session_saved(self, event: Event) -> None:
        path = event.payload.get("path", "?")
        self._write_line(f"[asat] saved session to {path}.")

    def _on_focus_changed(self, event: Event) -> None:
        cell_id = event.payload.get("new_cell_id")
        self._focus_cell_id = cell_id if isinstance(cell_id, str) else None
        if self._show_trace:
            mode = event.payload.get("new_mode", "?")
            suffix = (
                f" #{self._focus_cell_id[:6]}"
                if self._focus_cell_id is not None
                else ""
            )
            kind = event.payload.get("kind")
            heading_level = event.payload.get("heading_level")
            heading_title = event.payload.get("heading_title")
            if kind == "heading" and heading_level is not None and heading_title:
                self._write_line(
                    f"[{mode}{suffix} H{heading_level} {heading_title}]"
                )
            else:
                self._write_line(f"[{mode}{suffix}]")
        if self._show_outline:
            self._repaint_outline()

    def _on_cells_changed(self, event: Event) -> None:
        """Any structural mutation redraws the outline pane."""
        if self._show_outline:
            self._repaint_outline()

    def _repaint_outline(self) -> None:
        """Render the outline block and flush it to the stream.

        Real TTY streams get an ANSI clear-home first so the block
        replaces the previous view rather than stacking up; piped
        streams append each redraw so the history stays in order and
        tests can diff successive blocks.
        """
        if not self._show_outline or self._cells_provider is None:
            return
        cells = list(self._cells_provider())
        lines = render_outline(
            cells, self._focus_cell_id, max_width=self._outline_width
        )
        if not self._at_line_start:
            self._stream.write("\n")
            self._at_line_start = True
        if self._stream_is_tty() and self._show_trace is False:
            # Trace-off, outline-on is the "live dashboard" shape —
            # clear the screen so the outline is the only thing
            # visible. When trace is also on, leave the scrollback
            # alone so the user sees both history and the latest
            # outline.
            self._stream.write(_ANSI_CLEAR_HOME)
        self._stream.write(OUTLINE_HEADER + "\n")
        if not lines:
            self._stream.write(OUTLINE_EMPTY_LINE + "\n")
        else:
            for line in lines:
                self._stream.write(line + "\n")
        self._stream.write(OUTLINE_FOOTER + "\n")
        self._stream.flush()

    def _on_event_log_changed(self, event: Event) -> None:
        """Repaint the event-log panel on open/move/close/edit/replay."""
        self._repaint_event_log()

    def _repaint_event_log(self) -> None:
        """Render the event-log panel and flush it to the stream.

        Only paints while the viewer reports ``is_open`` so the panel
        disappears cleanly on close. Mirrors ``_repaint_outline`` —
        append-only on piped streams, ANSI-clear on TTY when no trace
        is stacking on top.
        """
        if self._event_log_viewer is None:
            return
        if not self._event_log_viewer.is_open:
            return
        entries = self._event_log_viewer.entries
        focus_index = self._event_log_viewer.focus_index
        tail = entries[-EVENT_LOG_TAIL_LINES:] if entries else ()
        first_index = len(entries) - len(tail)
        if not self._at_line_start:
            self._stream.write("\n")
            self._at_line_start = True
        if self._stream_is_tty() and self._show_trace is False:
            self._stream.write(_ANSI_CLEAR_HOME)
        self._stream.write(EVENT_LOG_HEADER + "\n")
        if not entries:
            self._stream.write(EVENT_LOG_EMPTY_LINE + "\n")
        else:
            for offset, entry in enumerate(tail):
                absolute = first_index + offset
                marker = "> " if absolute == focus_index else "  "
                self._stream.write(f"{marker}{entry.narration}\n")
            quick_field = self._event_log_viewer.quick_edit_field
            if quick_field is not None:
                buffer = self._event_log_viewer.quick_edit_buffer
                self._stream.write(
                    f"  [edit {quick_field}] {buffer}\n"
                )
        self._stream.write(EVENT_LOG_FOOTER + "\n")
        self._stream.flush()

    def _stream_is_tty(self) -> bool:
        """True when the output stream is attached to a real terminal."""
        isatty = getattr(self._stream, "isatty", None)
        if not callable(isatty):
            return False
        try:
            return bool(isatty())
        except (AttributeError, ValueError):
            return False

    def _on_action_invoked(self, event: Event) -> None:
        action = event.payload.get("action")
        if action == "insert_character":
            char = event.payload.get("char", "")
            if isinstance(char, str) and char:
                self._write_inline(char)
        elif action == "backspace":
            # Visually undo the last echoed character; harmless if the
            # cell already had no echoed content (the terminal just
            # beeps or clamps).
            self._write_inline("\b \b")
        elif action == "submit":
            if event.payload.get("meta_command") is None:
                command = event.payload.get("command", "")
                self._write_line(f"$ {command}")

    def _on_output_chunk(self, event: Event) -> None:
        line = event.payload.get("line", "")
        self._write_line(str(line))

    def _on_error_chunk(self, event: Event) -> None:
        line = event.payload.get("line", "")
        self._write_line(f"! {line}")

    def _on_command_completed(self, event: Event) -> None:
        exit_code = event.payload.get("exit_code", 0)
        self._write_line(f"[done exit={exit_code}]")

    def _on_command_failed(self, event: Event) -> None:
        payload = event.payload
        if "error" in payload:
            self._write_line(f"[failed: {payload['error']}]")
        else:
            exit_code = payload.get("exit_code", "?")
            self._write_line(f"[failed exit={exit_code}]")

    def _on_prompt_refresh(self, event: Event) -> None:
        payload = event.payload
        exit_code = payload.get("last_exit_code", "?")
        cwd = payload.get("cwd", "?")
        self._write_line(f"[prompt exit={exit_code} cwd={cwd}]")

    def _on_first_run(self, event: Event) -> None:
        lines = event.payload.get("lines", [])
        for line in lines:
            self._write_line(str(line))
