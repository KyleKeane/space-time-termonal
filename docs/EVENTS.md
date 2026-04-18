# Event reference

Every cross-module interaction in ASAT is an `Event` flowing through
the `EventBus`. This page is the authoritative list: every category,
the producer, and the payload fields a subscriber can rely on.

`EventType` values are defined in `asat/events.py`. Publishers build
and dispatch events via `publish_event(bus, event_type, payload, *,
source)` from `asat/event_bus.py`.

All events share the same envelope:

| Field        | Type                  | Notes                                         |
|--------------|-----------------------|-----------------------------------------------|
| `event_type` | `EventType`           | The category (see tables below).              |
| `payload`    | `dict[str, Any]`      | Shape depends on the category.                |
| `source`     | `str`                 | Short name of the publishing module.          |
| `timestamp`  | `datetime` (UTC)      | Set automatically at construction time.       |

When designing a new subscriber, key off `event_type` only. `source`
is informational (useful for debug logs or filtering) and can change
over time.

---

## Session lifecycle

Producer: `Session` serialization helpers (phase-2-onward caller code;
no module publishes these automatically yet).

| EventType         | Payload keys                      |
|-------------------|-----------------------------------|
| `SESSION_CREATED` | `session_id`                      |
| `SESSION_LOADED`  | `session_id`, `path`              |
| `SESSION_SAVED`   | `session_id`, `path`              |

## Cell lifecycle

Producer: `Session` mutators + `NotebookCursor` operations.

| EventType      | Payload keys                             |
|----------------|------------------------------------------|
| `CELL_CREATED` | `cell_id`, `command`                     |
| `CELL_UPDATED` | `cell_id`, `command`                     |
| `CELL_REMOVED` | `cell_id`                                |
| `CELL_MOVED`   | `cell_id`, `old_index`, `new_index`      |

## Execution kernel

Producer: `asat.kernel.ExecutionKernel` (`source="kernel"`).

| EventType           | Payload keys                                         |
|---------------------|------------------------------------------------------|
| `COMMAND_SUBMITTED` | `cell_id`, `command`                                 |
| `COMMAND_STARTED`   | `cell_id`                                            |
| `COMMAND_COMPLETED` | `cell_id`, `exit_code`, `timed_out`                  |
| `COMMAND_FAILED`    | `cell_id`, `exit_code`, `timed_out` or `error`/`error_type` when launch itself failed |
| `COMMAND_CANCELLED` | `cell_id`                                            |

## Output streaming

Producer: `asat.kernel.ExecutionKernel` as the subprocess streams.

| EventType      | Payload keys               |
|----------------|----------------------------|
| `OUTPUT_CHUNK` | `cell_id`, `line`          |
| `ERROR_CHUNK`  | `cell_id`, `line`          |

## Input + focus router

Producer: `asat.input_router.InputRouter` (`source="input_router"`)
and `asat.notebook.NotebookCursor` (`source="notebook"`).

| EventType        | Payload keys                                                                   | Source          |
|------------------|--------------------------------------------------------------------------------|-----------------|
| `FOCUS_CHANGED`  | `old_mode`, `new_mode`, `old_cell_id`, `new_cell_id`, `input_buffer`           | `notebook`      |
| `KEY_PRESSED`    | `name`, `char`, `modifiers`                                                    | `input_router`  |
| `ACTION_INVOKED` | `action`, `focus_mode`, `cell_id`, `key_name`, plus action-specific extras     | `input_router`  |

`ACTION_INVOKED` extras:

* `submit` → `cell_id` (of the submitted cell), `command`
* Every other action → no extras today

## Output buffering and cursor

Producers:

* `asat.output_buffer.OutputRecorder` (`source="output_recorder"`)
* `asat.output_cursor.OutputCursor` (`source="output_cursor"`)

| EventType              | Payload keys                                          |
|------------------------|-------------------------------------------------------|
| `OUTPUT_LINE_APPENDED` | `cell_id`, `line_number`, `stream`, `text`            |
| `OUTPUT_LINE_FOCUSED`  | `cell_id`, `line_number`, `stream`, `text`            |

`stream` is always one of `"stdout"` or `"stderr"`.

## Contextual action menu

Producer: `asat.actions.ActionMenu` (`source="action_menu"`) plus the
helper defined in `default_actions(...)` (`source="actions"`).

| EventType                   | Payload keys                                                   |
|-----------------------------|----------------------------------------------------------------|
| `ACTION_MENU_OPENED`        | `focus_mode`, `cell_id`, `item_ids`, `labels`                  |
| `ACTION_MENU_CLOSED`        | `focus_mode`, `cell_id`                                        |
| `ACTION_MENU_ITEM_FOCUSED`  | `item_id`, `label`, `index`                                    |
| `ACTION_MENU_ITEM_INVOKED`  | `item_id`, `label`, `focus_mode`, `cell_id`                    |

## Clipboard

Producer: `default_actions()` copy handlers (`source="actions"`).

| EventType          | Payload keys                             |
|--------------------|------------------------------------------|
| `CLIPBOARD_COPIED` | `cell_id`, `source`, `length`            |

`source` here is the *logical* label for what got copied
(`"line"`, `"all"`, `"stderr"`, etc.), not the publishing module.

## ANSI + interactive TUI mapping

Producer: `asat.tui_bridge.TuiBridge` (`source="tui_bridge"`).

| EventType                    | Payload keys                                                                 |
|------------------------------|------------------------------------------------------------------------------|
| `SCREEN_UPDATED`             | `cell_id`, `cursor_row`, `cursor_col`, `rows`                                |
| `INTERACTIVE_MENU_DETECTED`  | `cell_id`, `detection`, `selected_index`, `selected_text`, `items`           |
| `INTERACTIVE_MENU_UPDATED`   | same as `DETECTED`                                                           |
| `INTERACTIVE_MENU_CLEARED`   | `cell_id`                                                                    |

`detection` is one of `"reverse_video"` or `"prefix_marker"`. `items`
is a tuple of dicts with `row`, `text`, `selected`.

## Audio engine

Producer: `asat.sound_engine.SoundEngine` (`source="sound_engine"`).
See [AUDIO.md](AUDIO.md) for the full reference.

| EventType           | Payload keys                                                 |
|---------------------|--------------------------------------------------------------|
| `AUDIO_SPOKEN`      | `event_type`, `binding_id`, `text`, `voice_id`, `sound_id`   |
| `AUDIO_INTERRUPTED` | `event_type`                                                 |

## Settings editor

Producer: `asat.settings_editor.SettingsEditor`
(`source="settings_editor"`).

| EventType                | Payload keys                                                        |
|--------------------------|---------------------------------------------------------------------|
| `SETTINGS_OPENED`        | `section`, `record_count`                                           |
| `SETTINGS_CLOSED`        | `dirty`                                                             |
| `SETTINGS_FOCUSED`       | `level`, `section`, (optional) `record_index`, `record_id`, `field`, `value` |
| `SETTINGS_VALUE_EDITED`  | `section`, `record_index`, `field`, `old_value`, `new_value`        |
| `SETTINGS_SAVED`         | `path`                                                              |

---

## Adding a new event

1. Add the member to the `EventType` enum in `asat/events.py`.
2. Document the payload in this file under the appropriate section.
3. Publish it via `publish_event(bus, EventType.X, payload, source=...)`
   from the producing module. Never hand-roll `Event(...)` — the
   helper keeps construction uniform for future audit/correlation
   fields.
4. If the event is user-facing, add a default binding in the upcoming
   SoundBank so it has an audio reaction out of the box.
