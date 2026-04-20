"""Reference payloads for every covered EventType.

This dict is the single canonical answer to "what does an event
payload look like?". Two callers depend on it today:

* ``tests/test_default_bank.py`` uses it to confirm every default
  binding renders cleanly when handed a representative payload, so a
  template referencing ``{exit_code}`` cannot ship pointing at a key
  that isn't in any real publisher's output.
* ``asat/self_check.py`` (F42) replays one payload per
  ``COVERED_EVENT_TYPES`` member through the live engine + sink to
  prove a fresh install can actually produce audio.

The dict deliberately lives outside the test directory so production
code can import it without depending on ``tests/`` being on
``sys.path``. Adding a new covered event type means adding a row
here too — both the coverage test and the self-check otherwise
fail loudly, which is intentional.
"""

from __future__ import annotations

from asat.events import EventType


SAMPLE_PAYLOADS: dict[EventType, dict[str, object]] = {
    EventType.SESSION_CREATED: {"session_id": "s1"},
    EventType.SESSION_LOADED: {"session_id": "s1", "path": "/tmp/s.json"},
    EventType.SESSION_SAVED: {"session_id": "s1", "path": "/tmp/s.json"},
    EventType.CELL_CREATED: {"cell_id": "c1", "command": "ls"},
    EventType.CELL_UPDATED: {"cell_id": "c1", "command": "ls -la"},
    EventType.CELL_REMOVED: {"cell_id": "c1"},
    EventType.CELL_MOVED: {"cell_id": "c1", "old_index": 0, "new_index": 1},
    EventType.COMMAND_SUBMITTED: {"cell_id": "c1", "command": "ls"},
    EventType.COMMAND_STARTED: {"cell_id": "c1"},
    EventType.COMMAND_COMPLETED: {"cell_id": "c1", "exit_code": 0, "timed_out": False},
    EventType.COMMAND_COMPLETED_AWAY: {
        "cell_id": "c1",
        "current_cell_id": "c2",
        "original_event_type": "command.completed",
        "exit_code": 0,
        "timed_out": False,
    },
    EventType.COMMAND_FAILED: {"cell_id": "c1", "exit_code": 2, "timed_out": False},
    EventType.COMMAND_FAILED_STDERR_TAIL: {
        "cell_id": "c1",
        "exit_code": 1,
        "timed_out": False,
        "tail_lines": ["NameError: x"],
        "tail_text": "NameError: x",
        "line_count": 1,
    },
    EventType.COMMAND_CANCELLED: {"cell_id": "c1"},
    EventType.COMMAND_QUEUED: {"cell_id": "c1", "queue_depth": 1},
    EventType.QUEUE_DRAINED: {"last_cell_id": "c1", "queue_depth": 0},
    EventType.OUTPUT_CHUNK: {"cell_id": "c1", "line": "hello"},
    EventType.ERROR_CHUNK: {"cell_id": "c1", "line": "boom"},
    EventType.OUTPUT_STREAM_PAUSED: {"cell_id": "c1", "gap_sec": 5.2},
    EventType.OUTPUT_STREAM_BEAT: {"cell_id": "c1", "elapsed_sec": 30.0},
    EventType.FOCUS_CHANGED: {
        "old_mode": "notebook",
        "new_mode": "input",
        "old_cell_id": None,
        "new_cell_id": "c1",
        "input_buffer": "",
        "transition": "mode",
        "command": "",
        "kind": "command",
        "heading_level": None,
        "heading_title": None,
    },
    EventType.OUTPUT_LINE_FOCUSED: {
        "cell_id": "c1",
        "line_number": 1,
        "stream": "stdout",
        "text": "hello",
    },
    EventType.ACTION_MENU_OPENED: {
        "focus_mode": "notebook",
        "cell_id": "c1",
        "item_ids": ["a"],
        "labels": ["copy"],
    },
    EventType.ACTION_MENU_CLOSED: {"focus_mode": "notebook", "cell_id": "c1"},
    EventType.ACTION_MENU_ITEM_FOCUSED: {"item_id": "copy", "label": "Copy", "index": 0},
    EventType.ACTION_MENU_ITEM_INVOKED: {
        "item_id": "copy",
        "label": "Copy",
        "focus_mode": "notebook",
        "cell_id": "c1",
    },
    EventType.CLIPBOARD_COPIED: {"cell_id": "c1", "source": "line", "length": 42},
    EventType.INTERACTIVE_MENU_DETECTED: {
        "cell_id": "c1",
        "detection": "reverse_video",
        "selected_index": 0,
        "selected_text": "option 1",
        "items": [],
    },
    EventType.INTERACTIVE_MENU_UPDATED: {
        "cell_id": "c1",
        "detection": "reverse_video",
        "selected_index": 1,
        "selected_text": "option 2",
        "items": [],
    },
    EventType.INTERACTIVE_MENU_CLEARED: {"cell_id": "c1"},
    EventType.SETTINGS_OPENED: {"section": "voices", "record_count": 3},
    EventType.SETTINGS_CLOSED: {"dirty": False},
    EventType.SETTINGS_FOCUSED: {
        "level": "field",
        "section": "voices",
        "record_index": 0,
        "record_id": "narrator",
        "field": "rate",
        "value": 1.0,
    },
    EventType.SETTINGS_VALUE_EDITED: {
        "section": "voices",
        "record_index": 0,
        "field": "rate",
        "old_value": 1.0,
        "new_value": 1.1,
    },
    EventType.SETTINGS_SAVED: {"path": "/tmp/bank.json"},
    EventType.SETTINGS_SEARCH_OPENED: {
        "origin_level": "section",
        "origin_section": "voices",
        "origin_record_index": 0,
        "origin_field_index": 0,
    },
    EventType.SETTINGS_SEARCH_UPDATED: {
        "query": "nar",
        "match_count": 1,
        "section": "voices",
        "record_index": 0,
        "record_id": "narrator",
    },
    EventType.SETTINGS_SEARCH_CLOSED: {
        "query": "nar",
        "match_count": 1,
        "committed": True,
    },
    EventType.SETTINGS_RESET_OPENED: {
        "scope": "section",
        "section": "voices",
        "target_count": 3,
    },
    EventType.SETTINGS_RESET_CLOSED: {
        "scope": "section",
        "committed": True,
        "changed": True,
        "outcome": "applied",
    },
    EventType.HELP_REQUESTED: {"lines": ["help"]},
    EventType.PROMPT_REFRESH: {
        "last_exit_code": 1,
        "last_cell_id": "c1",
        "last_timed_out": False,
        "cwd": "/tmp",
    },
    EventType.FIRST_RUN_DETECTED: {
        "lines": ["Welcome."],
        "sentinel_path": "/tmp/first-run-done",
    },
    EventType.FIRST_RUN_TOUR_STEP: {
        "command": "echo hello, ASAT",
        "lines": [
            "Your first cell is ready.",
            "Press Enter to run it, or use Backspace to edit before running.",
        ],
    },
    EventType.WORKSPACE_OPENED: {
        "root": "/tmp/proj",
        "name": "proj",
        "notebook_count": 2,
    },
    EventType.NOTEBOOK_OPENED: {
        "path": "/tmp/proj/notebooks/default.asatnb",
        "name": "default",
    },
    EventType.NOTEBOOK_CREATED: {
        "path": "/tmp/proj/notebooks/ideas.asatnb",
        "name": "ideas",
    },
    EventType.NOTEBOOK_LISTED: {
        "names": ["default", "ideas"],
        "summary": "two notebooks: default, ideas",
    },
    EventType.BOOKMARK_CREATED: {"name": "setup", "cell_id": "c1"},
    EventType.BOOKMARK_JUMPED: {"name": "setup", "cell_id": "c1"},
    EventType.BOOKMARK_REMOVED: {"name": "setup", "cell_id": "c1"},
    EventType.VERBOSITY_CHANGED: {"level": "normal", "previous": "verbose"},
    EventType.BANK_RELOADED: {
        "path": "/home/user/.config/asat/bank.json",
        "binding_count": 42,
    },
    EventType.ANSI_OSC_RECEIVED: {
        "cell_id": "c1",
        "body": "133;A",
        "category": "prompt_start",
    },
}
