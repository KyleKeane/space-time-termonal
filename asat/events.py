"""Event types and the Event value object.

Events are immutable records describing something that happened in the
system. They are published to the EventBus, which routes them to any
subscribed handlers. Keeping events as plain data makes it trivial to
log, replay, or serialize an entire session for debugging.

Phase 1 defines only the event vocabulary. Later phases will emit these
events from the execution kernel, input router, audio engine, and
parser modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from asat.common import utcnow


class EventType(str, Enum):
    """All event categories the bus may carry.

    Grouped by producer for readability:

    Session lifecycle:
        SESSION_CREATED, SESSION_LOADED, SESSION_SAVED

    Cell lifecycle:
        CELL_CREATED, CELL_UPDATED, CELL_REMOVED, CELL_MOVED

    Execution kernel:
        COMMAND_SUBMITTED, COMMAND_STARTED, COMMAND_COMPLETED,
        COMMAND_FAILED, COMMAND_CANCELLED

    Output streaming:
        OUTPUT_CHUNK, ERROR_CHUNK

    Input and focus router:
        FOCUS_CHANGED, KEY_PRESSED, ACTION_INVOKED

    Output buffering and cursor:
        OUTPUT_LINE_APPENDED, OUTPUT_LINE_FOCUSED

    Contextual action menu:
        ACTION_MENU_OPENED, ACTION_MENU_CLOSED,
        ACTION_MENU_ITEM_FOCUSED, ACTION_MENU_ITEM_INVOKED

    Clipboard:
        CLIPBOARD_COPIED

    ANSI and interactive TUI mapping:
        SCREEN_UPDATED, INTERACTIVE_MENU_DETECTED,
        INTERACTIVE_MENU_UPDATED, INTERACTIVE_MENU_CLEARED,
        ANSI_CURSOR_MOVED, ANSI_SGR_CHANGED, ANSI_DISPLAY_CLEARED,
        ANSI_LINE_ERASED, ANSI_OSC_RECEIVED, ANSI_BELL

    Audio engine:
        AUDIO_SPOKEN, AUDIO_INTERRUPTED

    Help surface:
        HELP_REQUESTED

    Prompt context:
        PROMPT_REFRESH

    Onboarding:
        FIRST_RUN_DETECTED
    """

    SESSION_CREATED = "session.created"
    SESSION_LOADED = "session.loaded"
    SESSION_SAVED = "session.saved"

    CELL_CREATED = "cell.created"
    CELL_UPDATED = "cell.updated"
    CELL_REMOVED = "cell.removed"
    CELL_MOVED = "cell.moved"

    COMMAND_SUBMITTED = "command.submitted"
    COMMAND_STARTED = "command.started"
    COMMAND_COMPLETED = "command.completed"
    COMMAND_FAILED = "command.failed"
    COMMAND_CANCELLED = "command.cancelled"

    OUTPUT_CHUNK = "output.chunk"
    ERROR_CHUNK = "error.chunk"

    FOCUS_CHANGED = "focus.changed"
    KEY_PRESSED = "input.key"
    ACTION_INVOKED = "input.action"

    OUTPUT_LINE_APPENDED = "output.line.appended"
    OUTPUT_LINE_FOCUSED = "output.line.focused"

    ACTION_MENU_OPENED = "menu.opened"
    ACTION_MENU_CLOSED = "menu.closed"
    ACTION_MENU_ITEM_FOCUSED = "menu.item.focused"
    ACTION_MENU_ITEM_INVOKED = "menu.item.invoked"

    CLIPBOARD_COPIED = "clipboard.copied"

    SCREEN_UPDATED = "screen.updated"
    INTERACTIVE_MENU_DETECTED = "tui.menu.detected"
    INTERACTIVE_MENU_UPDATED = "tui.menu.updated"
    INTERACTIVE_MENU_CLEARED = "tui.menu.cleared"

    AUDIO_SPOKEN = "audio.spoken"
    AUDIO_INTERRUPTED = "audio.interrupted"

    HELP_REQUESTED = "help.requested"

    PROMPT_REFRESH = "prompt.refresh"

    FIRST_RUN_DETECTED = "onboarding.first_run"

    SETTINGS_OPENED = "settings.opened"
    SETTINGS_CLOSED = "settings.closed"
    SETTINGS_FOCUSED = "settings.focused"
    SETTINGS_VALUE_EDITED = "settings.value.edited"
    SETTINGS_SAVED = "settings.saved"
    SETTINGS_SEARCH_OPENED = "settings.search.opened"
    SETTINGS_SEARCH_UPDATED = "settings.search.updated"
    SETTINGS_SEARCH_CLOSED = "settings.search.closed"

    ANSI_CURSOR_MOVED = "ansi.cursor.moved"
    ANSI_SGR_CHANGED = "ansi.sgr.changed"
    ANSI_DISPLAY_CLEARED = "ansi.display.cleared"
    ANSI_LINE_ERASED = "ansi.line.erased"
    ANSI_OSC_RECEIVED = "ansi.osc.received"
    ANSI_BELL = "ansi.bell"


@dataclass(frozen=True)
class Event:
    """An immutable record of something that happened.

    event_type identifies the category. payload is an arbitrary
    dictionary of structured data associated with the event. source is
    a short string naming the producer module, useful when multiple
    producers publish the same event type. timestamp is set
    automatically at construction time.
    """

    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: datetime = field(default_factory=utcnow)
