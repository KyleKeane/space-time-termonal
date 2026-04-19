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

from enum import Enum
from typing import Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.output_buffer import OutputBuffer, OutputLine


DEFAULT_PAGE_SIZE = 10


class ComposerMode(str, Enum):
    """Which overlay the OUTPUT-mode composer is currently driving.

    Subclasses `str` so historic call sites (and tests) that compare
    against the literals "search" / "goto" keep working without
    needing to import the enum.
    """

    SEARCH = "search"
    GOTO = "goto"


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
        # F16 search / goto-line overlay. `_composer_mode` is None
        # unless the user is actively typing a search query or a line
        # number; in those states the router intercepts every key and
        # hands it to `extend_composer`, `backspace_composer`,
        # `commit_composer`, or `cancel_composer`. `_search_matches`
        # persists after commit so `next_match` / `prev_match` can keep
        # cycling through hits until the user searches again or
        # detaches the cursor.
        self._composer_mode: Optional[ComposerMode] = None
        self._composer_buffer: str = ""
        self._search_matches: tuple[int, ...] = ()
        self._search_position: int = -1
        self._composer_origin: int = -1
        self._last_search_query: str = ""

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
        self._clear_composer_state()
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
        self._clear_composer_state()

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

    @property
    def composer_mode(self) -> Optional[ComposerMode]:
        """Return ComposerMode.SEARCH, .GOTO, or None when idle.

        Members compare equal to the legacy strings ("search", "goto")
        for back-compat with callers and tests written before F49.
        """
        return self._composer_mode

    @property
    def composer_buffer(self) -> str:
        """Return the in-progress query / line number the user is typing."""
        return self._composer_buffer

    @property
    def search_query(self) -> str:
        """Return the query used by the most recent search (empty if none)."""
        return (
            self._composer_buffer
            if self._composer_mode is ComposerMode.SEARCH
            else self._last_search_query
        )

    @property
    def search_match_count(self) -> int:
        """Return the number of matches for the current / last search."""
        return len(self._search_matches)

    def begin_search(self) -> bool:
        """Enter SEARCH composer mode; returns False when no buffer lines."""
        return self._begin_composer(ComposerMode.SEARCH)

    def begin_goto(self) -> bool:
        """Enter GOTO-LINE composer mode; returns False when no buffer lines."""
        return self._begin_composer(ComposerMode.GOTO)

    def extend_composer(self, char: str) -> None:
        """Append a character to the composer buffer.

        In GOTO mode only digits are accepted; anything else is a silent
        no-op so a user who hits `g` and then a letter doesn't corrupt
        the line number buffer. In SEARCH mode the match list is
        recomputed on every keystroke and the cursor jumps to the first
        match so listening to the buffer narrates the hits live.
        """
        if self._composer_mode is None:
            return
        if len(char) != 1:
            raise ValueError("extend_composer expects exactly one character")
        if self._composer_mode is ComposerMode.GOTO and not char.isdigit():
            return
        self._composer_buffer += char
        if self._composer_mode is ComposerMode.SEARCH:
            self._recompute_matches(jump_to_first=True)

    def backspace_composer(self) -> None:
        """Trim one character from the composer buffer."""
        if self._composer_mode is None or not self._composer_buffer:
            return
        self._composer_buffer = self._composer_buffer[:-1]
        if self._composer_mode is ComposerMode.SEARCH:
            self._recompute_matches(jump_to_first=True)

    def commit_composer(self) -> Optional[OutputLine]:
        """Apply the composer: jump to the parsed line or stay on match.

        For SEARCH, the cursor is already sitting on the current match
        (kept in sync by `extend_composer`); commit just closes the
        composer and preserves `_search_matches` so `next_match` /
        `prev_match` keep working. For GOTO, parse the buffer as 1-
        based line number, clamp, and seek there.
        """
        mode = self._composer_mode
        if mode is None:
            return None
        buffer_text = self._composer_buffer
        # State transition: SEARCH or GOTO → idle (apply branch). For
        # SEARCH the cursor stays where the live-jump put it; for GOTO
        # we seek to the parsed line below. See
        # docs/USER_MANUAL.md "OUTPUT mode — re-reading output".
        self._composer_mode = None
        if mode is ComposerMode.SEARCH:
            self._composer_buffer = ""
            self._last_search_query = buffer_text
            return self.current_line()
        self._composer_buffer = ""
        if not buffer_text:
            return None
        try:
            target_one_based = int(buffer_text)
        except ValueError:
            return None
        return self._seek(target_one_based - 1)

    def cancel_composer(self) -> Optional[OutputLine]:
        """Discard the composer and restore the line the user started on."""
        if self._composer_mode is None:
            return None
        origin = self._composer_origin
        self._clear_composer_state()
        if origin < 0:
            return self.current_line()
        return self._seek(origin)

    def next_match(self) -> Optional[OutputLine]:
        """Cycle to the next search match; no-op if there are none."""
        if not self._search_matches:
            return None
        self._search_position = (self._search_position + 1) % len(self._search_matches)
        return self._seek(self._search_matches[self._search_position])

    def prev_match(self) -> Optional[OutputLine]:
        """Cycle to the previous search match; no-op if there are none."""
        if not self._search_matches:
            return None
        self._search_position = (self._search_position - 1) % len(self._search_matches)
        return self._seek(self._search_matches[self._search_position])

    def jump_to_line(self, line_number: int) -> Optional[OutputLine]:
        """Focus the given zero-based line number (clamped to the buffer)."""
        return self._seek(line_number)

    def _begin_composer(self, mode: ComposerMode) -> bool:
        """Shared entry point for `begin_search` and `begin_goto`."""
        if self._buffer is None or len(self._buffer) == 0:
            return False
        # State transition: idle → SEARCH or GOTO sub-mode. The router
        # dispatches every keystroke through the composer key path
        # while `_composer_mode` is non-None. See
        # docs/USER_MANUAL.md "OUTPUT mode — re-reading output".
        self._composer_mode = mode
        self._composer_buffer = ""
        self._composer_origin = self._index
        if mode is ComposerMode.SEARCH:
            self._search_matches = ()
            self._search_position = -1
        return True

    def _clear_composer_state(self) -> None:
        """Reset every composer field back to its detached defaults."""
        # State transition: SEARCH or GOTO → idle. After this returns
        # the router resumes regular OUTPUT-mode dispatch. See
        # docs/USER_MANUAL.md "OUTPUT mode — re-reading output".
        self._composer_mode = None
        self._composer_buffer = ""
        self._search_matches = ()
        self._search_position = -1
        self._composer_origin = -1
        self._last_search_query = ""

    def _recompute_matches(self, jump_to_first: bool) -> None:
        """Rebuild the match list from the current buffer and query."""
        if self._buffer is None or not self._composer_buffer:
            self._search_matches = ()
            self._search_position = -1
            return
        query = self._composer_buffer.lower()
        matches = tuple(
            line.line_number
            for line in self._buffer
            if query in line.text.lower()
        )
        self._search_matches = matches
        if not matches:
            self._search_position = -1
            return
        if jump_to_first:
            self._search_position = 0
            self._seek(matches[0])

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
