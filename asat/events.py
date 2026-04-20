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

    Execution queue (F62):
        COMMAND_QUEUED fires the instant a submission is accepted by
        the `ExecutionWorker`, carrying `queue_depth` so an audio cue
        can narrate "queued, <N> ahead". QUEUE_DRAINED fires when the
        worker has no pending work left.

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
        AUDIO_SPOKEN, AUDIO_INTERRUPTED, NARRATION_REPLAYED

    Help surface:
        HELP_REQUESTED

    Prompt context:
        PROMPT_REFRESH

    Onboarding:
        FIRST_RUN_DETECTED, FIRST_RUN_TOUR_STEP (F43: the guided first-
        command tour event that narrates "press Enter to run your first
        command" while the notebook's first cell is pre-populated with
        ``echo hello, ASAT``).

    Workspace (F50):
        WORKSPACE_OPENED fires once on launch with the resolved
        workspace root and the count of notebooks found.
        NOTEBOOK_OPENED fires each time a notebook is loaded into
        the active session (today: once per launch; post-F29 tabs
        will fire it on every tab switch). NOTEBOOK_CREATED fires
        when ``:new-notebook`` or ``Workspace.new_notebook`` writes
        a fresh file. NOTEBOOK_LISTED is the ambient response to
        ``:list-notebooks``.

    Completion alerts:
        COMMAND_COMPLETED_AWAY (fires alongside COMMAND_COMPLETED /
        COMMAND_FAILED when the user's focus has moved to a different
        cell between submission and completion; F34).

    Cell bookmarks (F35):
        BOOKMARK_CREATED fires when ``:bookmark <name>`` captures the
        focused cell. BOOKMARK_JUMPED fires when ``:jump <name>`` moves
        focus to a previously bookmarked cell. BOOKMARK_REMOVED fires
        when ``:unbookmark <name>`` deletes a bookmark, or when the
        underlying cell is removed and the registry prunes the dangling
        entry. Each payload carries the bookmark ``name`` and (when
        applicable) the resolved ``cell_id``.

    Self-check (F42):
        SELF_CHECK_STEP fires once per step of the ``--check``
        diagnostic self-test. Payload carries ``step`` (slug),
        ``status`` (``"pass"`` / ``"fail"`` / ``"skip"``), ``index``
        / ``total`` (1-based step counter), and ``detail`` (a short
        free-text summary so the JSONL log is human-skimmable).

    Bank reload (F3):
        BANK_RELOADED fires after ``:reload-bank`` re-reads the
        on-disk bank and swaps the live bank in. Payload carries
        ``path`` (resolved string) and ``binding_count`` so the user
        hears confirmation that the edits-in-progress were discarded.

    Continuous output playback (F24):
        OUTPUT_PLAYBACK_STARTED fires when the user presses ``p`` /
        ``Space`` in OUTPUT mode and the auto-advance ticker begins.
        OUTPUT_PLAYBACK_STOPPED fires when playback ends — whether the
        cursor reached the bottom (``reason="end"``), the user tapped
        any key (``"cancelled"``), focus left OUTPUT mode
        (``"focus_changed"``), or the buffer was re-attached
        (``"detached"``). Payload also carries ``cell_id`` and, on
        start, ``interval_sec``.
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
    COMMAND_COMPLETED_AWAY = "command.completed.away"
    COMMAND_FAILED = "command.failed"
    COMMAND_FAILED_STDERR_TAIL = "command.failed.stderr_tail"
    COMMAND_CANCELLED = "command.cancelled"
    COMMAND_QUEUED = "command.queued"
    QUEUE_DRAINED = "queue.drained"

    OUTPUT_CHUNK = "output.chunk"
    ERROR_CHUNK = "error.chunk"
    OUTPUT_STREAM_PAUSED = "output.stream.paused"
    OUTPUT_STREAM_BEAT = "output.stream.beat"

    FOCUS_CHANGED = "focus.changed"
    OUTLINE_FOLDED = "outline.folded"
    OUTLINE_UNFOLDED = "outline.unfolded"
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
    NARRATION_REPLAYED = "audio.narration.replayed"

    HELP_REQUESTED = "help.requested"

    PROMPT_REFRESH = "prompt.refresh"

    FIRST_RUN_DETECTED = "onboarding.first_run"
    FIRST_RUN_TOUR_STEP = "onboarding.first_run.tour_step"

    WORKSPACE_OPENED = "workspace.opened"
    NOTEBOOK_OPENED = "workspace.notebook.opened"
    NOTEBOOK_CREATED = "workspace.notebook.created"
    NOTEBOOK_LISTED = "workspace.notebook.listed"

    BOOKMARK_CREATED = "bookmark.created"
    BOOKMARK_JUMPED = "bookmark.jumped"
    BOOKMARK_REMOVED = "bookmark.removed"

    SETTINGS_OPENED = "settings.opened"
    SETTINGS_CLOSED = "settings.closed"
    SETTINGS_FOCUSED = "settings.focused"
    SETTINGS_VALUE_EDITED = "settings.value.edited"
    SETTINGS_SAVED = "settings.saved"
    SETTINGS_SEARCH_OPENED = "settings.search.opened"
    SETTINGS_SEARCH_UPDATED = "settings.search.updated"
    SETTINGS_SEARCH_CLOSED = "settings.search.closed"
    SETTINGS_RESET_OPENED = "settings.reset.opened"
    SETTINGS_RESET_CLOSED = "settings.reset.closed"

    ANSI_CURSOR_MOVED = "ansi.cursor.moved"
    ANSI_SGR_CHANGED = "ansi.sgr.changed"
    ANSI_DISPLAY_CLEARED = "ansi.display.cleared"
    ANSI_LINE_ERASED = "ansi.line.erased"
    ANSI_OSC_RECEIVED = "ansi.osc.received"
    ANSI_BELL = "ansi.bell"

    SELF_CHECK_STEP = "self_check.step"

    VERBOSITY_CHANGED = "verbosity.changed"

    BANK_RELOADED = "bank.reloaded"

    OUTPUT_PLAYBACK_STARTED = "output.playback.started"
    OUTPUT_PLAYBACK_STOPPED = "output.playback.stopped"

    EVENT_LOG_OPENED = "event_log.opened"
    EVENT_LOG_CLOSED = "event_log.closed"
    EVENT_LOG_FOCUSED = "event_log.focused"
    EVENT_LOG_QUICK_EDIT_COMMITTED = "event_log.quick_edit.committed"
    EVENT_LOG_REPLAYED = "event_log.replayed"


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
