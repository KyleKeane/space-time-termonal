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

Producer: caller code that owns a `Session` — today ASAT does not yet
ship an automatic publisher for these. Embedding code (the eventual
entry-point binary) is expected to fire them around `Session`
construction, `Session.load(...)`, and `Session.save(...)`. Bindings
in the default bank are already wired so narration lights up the
instant a producer appears.

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

| EventType                     | Payload keys                                         |
|-------------------------------|------------------------------------------------------|
| `COMMAND_SUBMITTED`           | `cell_id`, `command`                                 |
| `COMMAND_STARTED`             | `cell_id`                                            |
| `COMMAND_COMPLETED`           | `cell_id`, `exit_code`, `timed_out`                  |
| `COMMAND_FAILED`              | `cell_id`, `exit_code`, `timed_out` or `error`/`error_type` when launch itself failed |
| `COMMAND_FAILED_STDERR_TAIL`  | `cell_id`, `exit_code`, `timed_out`, `tail_lines`, `tail_text`, `line_count` |
| `COMMAND_COMPLETED_AWAY`      | `cell_id`, `current_cell_id`, `original_event_type`, `exit_code`, `timed_out` |
| `COMMAND_CANCELLED`           | `cell_id`                                            |

`COMMAND_FAILED_STDERR_TAIL` is a secondary event produced by
`asat.error_tail.StderrTailAnnouncer` (`source="error_tail"`) a beat
after `COMMAND_FAILED`. It carries the last N stderr lines captured
by the `OutputRecorder` so a binding can narrate the actual error
text. Only fires when the failed cell produced at least one stderr
line; see [FEATURE_REQUESTS.md#f36](FEATURE_REQUESTS.md#f36).

`COMMAND_COMPLETED_AWAY` is a secondary event produced by
`asat.completion_alert.CompletionFocusWatcher`
(`source="completion_alert"`) whenever `COMMAND_COMPLETED` or
`COMMAND_FAILED` fires with a `cell_id` that differs from the
user's current focus. It carries the `original_event_type` string
(`"command.completed"` or `"command.failed"`) so one binding can
cover both paths. See
[FEATURE_REQUESTS.md#f34](FEATURE_REQUESTS.md#f34).

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
| `FOCUS_CHANGED`  | `old_mode`, `new_mode`, `old_cell_id`, `new_cell_id`, `input_buffer`, `transition`, `command` | `notebook`      |
| `KEY_PRESSED`    | `name`, `char`, `modifiers`                                                    | `input_router`  |
| `ACTION_INVOKED` | `action`, `focus_mode`, `cell_id`, `key_name`, plus action-specific extras     | `input_router`  |

`FOCUS_CHANGED` extras:

* `transition` is `"mode"` when the focus mode changed, `"cell"` when
  only the focused cell changed within NOTEBOOK. Buffer-only deltas
  (characters typed into the input buffer) do NOT publish
  `FOCUS_CHANGED`; subscribe to `ACTION_INVOKED` with
  `action == "insert_character"` instead.
* `command` is the focused cell's current command text at transition
  time, or `""` when no cell is focused. Bindings can narrate it with
  a `{command}` template.

`ACTION_INVOKED` extras:

* `submit` → `cell_id` (of the submitted cell), `command`
* `insert_character` → `char` (the literal character that was inserted)
* `settings_edit_extend` → `char` (the literal character appended to
  the in-progress settings edit buffer)
* Every other action → no extras

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
| `ANSI_CURSOR_MOVED`          | `cell_id`, `reason`, `old_row`, `old_col`, `new_row`, `new_col`, `params`    |
| `ANSI_SGR_CHANGED`           | `cell_id`, `params`, `attrs_added`, `attrs_removed`, `current_attrs`         |
| `ANSI_DISPLAY_CLEARED`       | `cell_id`, `mode`                                                            |
| `ANSI_LINE_ERASED`           | `cell_id`, `mode`                                                            |
| `ANSI_OSC_RECEIVED`          | `cell_id`, `body`, `category`                                                |
| `ANSI_BELL`                  | `cell_id`                                                                    |

`detection` is one of `"reverse_video"` or `"prefix_marker"`. `items`
is a tuple of dicts with `row`, `text`, `selected`.

`ANSI_CURSOR_MOVED.reason` is one of `"up"`, `"down"`, `"forward"`,
`"back"`, `"next_line"`, `"previous_line"`, `"column"`, `"absolute"` —
the semantic of the CSI final byte, so bindings can filter on intent
without re-parsing the raw sequence. Row and column numbers are
zero-based in the post-apply screen frame. `params` carries the
decimal CSI parameters verbatim.

`ANSI_SGR_CHANGED.attrs_added` / `attrs_removed` are sorted lists of
the attribute names that changed on this SGR (`"bold"`, `"italic"`,
`"underline"`, `"reverse"`, `"dim"`, `"strikethrough"`, etc.). The
`current_attrs` field is the full post-apply set for callers that want
absolute state.

`ANSI_DISPLAY_CLEARED.mode` is the `J` parameter (0 = cursor-to-end,
1 = start-to-cursor, 2 = entire screen, 3 = plus scrollback).
`ANSI_LINE_ERASED.mode` is the `K` parameter (0, 1, 2).

`ANSI_OSC_RECEIVED.category` is one of `"title"` (OSC 0/1/2),
`"hyperlink"` (OSC 8), `"color"` (OSC 4/10/11), or `"other"`. The raw
`body` is included so advanced bindings can match on specific
subcommands.

`ANSI_BELL` carries no extra data: it fires once per BEL byte (0x07)
unless the byte is the OSC terminator.

## Audio engine

Producer: `asat.sound_engine.SoundEngine` (`source="sound_engine"`).
See [AUDIO.md](AUDIO.md) for the full reference.

| EventType           | Payload keys                                                 |
|---------------------|--------------------------------------------------------------|
| `AUDIO_SPOKEN`      | `event_type`, `binding_id`, `text`, `voice_id`, `sound_id`   |
| `AUDIO_INTERRUPTED` | `event_type`                                                 |
| `NARRATION_REPLAYED`| `event_type`, `binding_id`, `text`, `voice_id`               |

`NARRATION_REPLAYED` fires when the user presses `Ctrl+R` (or
types `:repeat`) to re-hear the most recent narration. The engine
bypasses bindings on replay so this event does not itself add a
new entry to the history buffer; see
[FEATURE_REQUESTS.md#f30](FEATURE_REQUESTS.md#f30).

## Settings editor

Producer: `asat.settings_editor.SettingsEditor`
(`source="settings_editor"`).

| EventType                  | Payload keys                                                        |
|----------------------------|---------------------------------------------------------------------|
| `SETTINGS_OPENED`          | `section`, `record_count`                                           |
| `SETTINGS_CLOSED`          | `dirty`                                                             |
| `SETTINGS_FOCUSED`         | `level`, `section`, (optional) `record_index`, `record_id`, `field`, `value` |
| `SETTINGS_VALUE_EDITED`    | `section`, `record_index`, `field`, `old_value`, `new_value`        |
| `SETTINGS_SAVED`           | `path`                                                              |
| `SETTINGS_SEARCH_OPENED`   | `origin_level`, `origin_section`, `origin_record_index`, `origin_field_index` |
| `SETTINGS_SEARCH_UPDATED`  | `query`, `match_count`, (optional, when matched) `section`, `record_index`, `record_id` |
| `SETTINGS_SEARCH_CLOSED`   | `query`, `match_count`, `committed`                                 |
| `SETTINGS_RESET_OPENED`    | `scope` (`field`/`record`/`section`/`bank`), `section`, `target_count`, (when scope ∈ {`field`, `record`}) `record_id`, (when scope=`field`) `field` |
| `SETTINGS_RESET_CLOSED`    | `scope`, `committed`, `changed`, `outcome` (`applied`/`already_default`/`cancelled`) |

The `SETTINGS_SEARCH_*` family fires from the `/` search overlay
described in [USER_MANUAL.md § Searching the bank](USER_MANUAL.md#searching-the-bank).
`SETTINGS_SEARCH_UPDATED` publishes on every buffer edit, including
zero-match queries (so narration can announce "0 matches").
`SETTINGS_SEARCH_CLOSED` carries `committed=True` on Enter and
`committed=False` on Escape / ascend cancel.

The `SETTINGS_RESET_*` family fires from the Ctrl+R / `:reset`
confirmation sub-mode described in
[USER_MANUAL.md § Resetting to defaults](USER_MANUAL.md#resetting-to-defaults).
`target_count` on `SETTINGS_RESET_OPENED` is the number of records
the confirmation would touch (1 for field/record scope, the section
length for section scope, and the total record count across every
section for bank scope). `SETTINGS_RESET_CLOSED`'s `outcome` key
discriminates the three end states so each can fire its own
single-clause predicate binding: `applied` when the reset mutated
the bank, `already_default` when the targeted slice already matched
defaults (no history entry is pushed), and `cancelled` when the
user escaped out of the confirmation.

## Help surface

Producer: `asat.input_router.InputRouter` (`source="input_router"`),
fired when the user submits the `:help` meta-command.

| EventType        | Payload keys                                     |
|------------------|--------------------------------------------------|
| `HELP_REQUESTED` | `lines` (list of str: the cheat-sheet lines from `asat.input_router.HELP_LINES`) |

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
