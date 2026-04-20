# ASAT Architecture

This document describes how the Accessible Spatial Audio Terminal is
organised. It is the first file a new contributor should read. Every
design choice prioritises blind developers on Windows using a screen
reader: keyboard-only operation, predictable event ordering, and
self-voicing feedback without ever depending on colour or layout.

The guiding principles, simplicity checklist, and PR recipe every
contribution follows live in
[`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md); this document stays
focused on *how the code is wired*. Read the developer guide before
your first PR and keep it open alongside the roadmap in
[`FEATURE_REQUESTS.md`](FEATURE_REQUESTS.md).

## Goals

* Self-voicing: every state change produces an auditable event.
* Keyboard-first: no action requires a mouse, a pointer, or screen
  coordinates.
* Standard library only (Python >= 3.10): no third-party runtime
  dependencies. `numpy` is used opportunistically by `asat/hrtf.py`
  as an optional accelerator for dense (measured) HRTF kernels; the
  synthetic profiles shipped by default run pure-Python on a
  sparse-impulse fast path. External backends (TTS engines, sound
  output) plug in behind small `Protocol`-based interfaces.
* Hyper-modular: each concern lives in its own file so contributions
  can land independently and each module can be read in one sitting.
* Deterministic: the `EventBus` runs synchronously and publishes to
  handlers in registration order so tests and audio output never race.

## Layered module map

Modules are grouped here by layer. Arrows point from a caller to the
module it depends on. Nothing below a layer calls into the layer above
it; the event bus is the sole backchannel.

```
           +-----------------------------+
           |  Entry point                |
           |  __main__.py  app.py        |
           |  keyboard.py  terminal.py   |
           |  outline.py  self_check.py  |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Presentation               |
           |  settings_controller.py     |
           |  settings_editor.py         |
           |  event_log.py               |
           |  onboarding.py              |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Audio framework            |
           |  sound_engine.py            |
           |  sound_generators.py        |
           |  sound_bank.py              |
           |  default_bank.py            |
           |  sample_payloads.py         |
           |  audio.py  audio_sink.py    |
           |  tts.py  tts_registry.py    |
           |  hrtf.py                    |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Interaction layer          |
           |  input_router.py            |
           |  notebook.py  actions.py    |
           |  output_cursor.py           |
           |  output_playback.py         |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Parsing & TUI bridge       |
           |  ansi.py  screen.py         |
           |  interactive.py             |
           |  tui_bridge.py              |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Execution kernel           |
           |  kernel.py  runner.py       |
           |  execution.py               |
           |  execution_worker.py        |
           |  shell_backend.py           |
           |  streaming_monitor.py       |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Data + bus (foundation)    |
           |  cell.py  session.py        |
           |  events.py  event_bus.py    |
           |  event_log_file.py          |
           |  output_buffer.py           |
           |  workspace.py               |
           |  common.py  keys.py         |
           +-----------------------------+
```

## Core value objects

* `Cell` (`asat/cell.py`) – one notebook input/output interaction.
  The only non-frozen core dataclass. See `asat/cell.py` docstring for
  the mutation policy; subscribers that need a stable view call
  `Cell.snapshot()`.
* `Session` (`asat/session.py`) – ordered list of cells with save /
  load.
* `Event` (`asat/events.py`) – immutable `(event_type, payload,
  source, timestamp)` record. Every publisher creates one via the
  `publish_event(...)` helper in `asat/event_bus.py`.
* `Key` (`asat/keys.py`) – frozen `(name, char, modifiers)` triple.
  All input goes through this shape before reaching the router, which
  keeps platform adapters pluggable.

## Event bus

`EventBus` (`asat/event_bus.py`) is a synchronous in-process
publish/subscribe router. Subscribers register with either a concrete
`EventType` or the `WILDCARD` ("*") string. Publishers use the
`publish_event(bus, event_type, payload, *, source)` helper so the
boilerplate of building `Event` objects stays in one place.

Publishing order is strictly exact-type subscribers first, then
wildcard subscribers. If any handler raises, the bus collects
exceptions and re-raises an `EventBusError` after every handler has
had a chance to react. This matters for audio: a broken voice must
never silence the rest of the pipeline.

## Event categories

The authoritative list lives in `asat/events.py`. See
[EVENTS.md](EVENTS.md) for each category, the producer, and the
typical payload shape.

## Focus model

The user is always in exactly one of four focus modes, owned by
`NotebookCursor` (`asat/notebook.py`):

* `NOTEBOOK` – walking between cells.
* `INPUT` – typing a command into the active cell.
* `OUTPUT` – stepping line-by-line through a cell's captured output.
* `SETTINGS` – driving the `SettingsEditor` via `SettingsController`.

Mode transitions publish `FOCUS_CHANGED`. The `InputRouter`
(`asat/input_router.py`) looks up a keystroke under the current mode
to find an action name, runs the matching handler, and publishes
`KEY_PRESSED` + `ACTION_INVOKED`. The default binding table per mode
is printed in [USER_MANUAL.md](USER_MANUAL.md#the-keystroke-cheat-sheet);
the authoritative source is `default_bindings()` in
`asat/input_router.py`.

## Execution path

1. User types a command; `InputRouter` runs the `submit` action.
2. `NotebookCursor.submit()` commits the input buffer into the
   focused Cell and returns it.
3. `ExecutionKernel.run(cell)` publishes `COMMAND_SUBMITTED`,
   `COMMAND_STARTED`, streams `OUTPUT_CHUNK` / `ERROR_CHUNK`, then
   either `COMMAND_COMPLETED` (exit code 0) or `COMMAND_FAILED`.
4. `OutputRecorder` (`asat/output_buffer.py`) subscribes to the chunk
   events and fills per-cell `OutputBuffer` instances, re-publishing
   `OUTPUT_LINE_APPENDED` with structured per-line data.
5. `TuiBridge` (`asat/tui_bridge.py`) can also consume raw stream text
   to detect interactive menus via `AnsiParser` + `VirtualScreen` +
   `interactive.detect(...)`, emitting `SCREEN_UPDATED` and the
   `INTERACTIVE_MENU_*` lifecycle events.

## Audio pipeline

`SoundEngine` (`asat/sound_engine.py`) subscribes to every event the
active `SoundBank` mentions, renders templates + predicates, pipes
speech through the `TTSEngine`, runs recipes through
`SoundGeneratorRegistry`, spatialises both via the `Spatializer`, and
hands the mix to the `AudioSink`. `default_sound_bank()` seeds the
baseline, and `SettingsEditor` plus `SettingsController` let users
reshape the bank live from the keyboard without restarting. See
[AUDIO.md](AUDIO.md) for the full reference.

The TTS engine is pluggable via `TTSEngineRegistry` in
`asat/tts_registry.py`. The default priority is `pyttsx3` →
`espeak-ng` → macOS `say` → Windows SAPI → the deterministic tone
fallback. `:tts list` / `:tts use <id>` / `:tts set <param> <value>`
swap engines and parameters live without restarting.

## MVP surfaces (2026-04 roadmap)

The four user-observable surfaces promised by the 2026-04 MVP
stabilization roadmap are wired as follows — each one is one
direction of a dependency edge in the diagram above.

- **Live audio on launch.** `audio_sink.pick_live_sink()` returns
  `WindowsLiveAudioSink` on Windows and `PosixLiveAudioSink` (pipes
  WAV to `aplay` / `paplay` / `afplay`) on POSIX; `tts_registry.
  select_default()` picks the first installed TTS adapter. `--live`
  is the default on a TTY; `--no-live` opts out.
- **On-screen outline pane.** `outline.render_outline()` is pure and
  unit-tested; `terminal.TerminalRenderer` subscribes to
  `FOCUS_CHANGED` / `CELL_*` / outline-fold events and repaints the
  pane (ANSI-clear redraw on TTY, append-only trace when piped).
  `--view {trace,outline,both}` picks panes.
- **Interactive event log.** `event_log.EventLogViewer` holds a
  bounded ring (200 entries) of wildcard-subscribed events,
  re-narrates them on navigation, and dispatches the `e` / `t` /
  `Enter` quick-edit / replay / jump-to-binding actions.
  `event_log_file.EventLogFile` is a separate wildcard subscriber
  that writes the grouped daily text log.
- **Scripted first-run tour.** `onboarding.OnboardingCoordinator`
  owns the five published beats; `app.Application.build` seeds the
  three-cell demo notebook on first run and calls `_run_scripted_
  tour(replay=False)`. `:welcome` invokes `_replay_welcome`, which
  re-runs the tour with `replay=True` and does **not** re-seed cells.

## Sync gates

Several test guards fail when code and docs drift. Each new event
type, binding, or user-visible surface must satisfy every gate that
applies:

| Gate                                                | What it checks                                                                                                        | How to satisfy when adding a new event type                                                                                   |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `tests/test_events_docs_sync.py`                    | Every `EventType` member is mentioned in `docs/EVENTS.md`.                                                            | Add a row + prose under the appropriate category in `docs/EVENTS.md`.                                                         |
| `tests/test_default_bank.py::CoverageTests`         | Every `COVERED_EVENT_TYPES` member has a reference payload in `asat/sample_payloads.py` and renders cleanly.          | Add an entry to `SAMPLE_PAYLOADS` keyed by the new `EventType`; add a binding to `default_bank.py` and list it in `COVERED_EVENT_TYPES`. |
| `tests/test_user_manual_sync.py`                    | Keystrokes, meta-commands, and focus-mode bindings referenced in `docs/USER_MANUAL.md` match the code.                | Update the relevant table in `USER_MANUAL.md` when you change a binding.                                                      |
| `tests/test_bindings_introspection.py::BindingsDocInSyncTests` | `docs/BINDINGS.md` matches the output of `python -m asat.tools.render_bindings_doc`.                                   | Regenerate: `python -m asat.tools.render_bindings_doc > docs/BINDINGS.md`.                                                     |
| `tests/test_default_bank_orphans.py`                | No `EventBinding.event_type` references an `EventType` no producer emits.                                             | Wire a publisher (or delete the orphan binding).                                                                              |

`asat/self_check.py` — invoked by `python -m asat --check` — replays
one `SAMPLE_PAYLOADS` entry per `COVERED_EVENT_TYPES` member through
the live engine and sink, so a fresh install can prove end-to-end
that audio works before the user hits the keyboard.

## Predicate DSL

`EventBinding.predicate` (see `asat/sound_engine.py`,
`DefaultPredicateEvaluator`) is a deliberately tiny string language,
not Python. The grammar is **strictly** `key op literal`, one
clause, no attribute access:

- `key` is a top-level payload key (e.g. `exit_code`, `path`, `kind`).
- `op` is one of `==`, `!=`, `in` (the `in` operator takes a
  comma-separated list literal).
- `literal` is a string (single or double quoted), a bare integer,
  a bare float, `true` / `false`, or an unquoted token compared as a
  string.

Examples: `exit_code != 0`, `kind == 'heading'`, `path != ''`,
`category in 'prompt_start,prompt_end'`.

Things the DSL does **not** support: nested attribute access
(`payload.path`), logical operators (`and` / `or` / `not`), numeric
comparisons other than equality, function calls, or arbitrary Python
expressions. Gating on a non-empty string is the `path != ''` idiom
shown above; gating on presence of a key is not expressible — give
the payload a well-known empty default instead.

## Testing

Every module ships a matching `tests/test_<module>.py` built on
`unittest`. The full suite runs in under a second and is the
expectation on every PR:

```
python -m unittest discover -s tests -t .
```

## Phase history

The repo grew in strict phase-gated PRs. Each phase added a single
layer and left the one below it unchanged:

| Phase | Theme                                          |
|-------|------------------------------------------------|
| 1     | Data models + event bus                        |
| 2     | Execution kernel + subprocess runner           |
| 3     | Audio primitives (TTS, HRTF, sinks)            |
| 4     | Input router + notebook cursor                 |
| 5     | Output buffering + contextual action menu      |
| 6     | ANSI parsing + virtual screen + menu detection |
| A     | Data-driven audio framework (generators, engine, default bank, editor) |
| T     | Follow-up polish: TUI wiring, ANSI-level events + per-binding overrides, docs |
| H     | HRTF sparse-impulse fast path (synthetic profiles bypass full convolution) |
| E     | End-to-end entry point: Application wiring, keyboard adapter, `python -m asat` |
| E2    | First-launch polish: TerminalRenderer, SESSION_* publishes, WindowsLiveAudioSink, `--live` / `--quiet` |
| E3    | Onboarding polish: non-TTY guard, sink signpost, `:help` meta-command + `HELP_REQUESTED`, `--version` / `--check` |

## Entry point

`asat/app.py` defines an `Application` dataclass that assembles every
collaborator into one object; `asat/__main__.py` (invoked via
`python -m asat`) builds one with platform-default I/O and drives a
synchronous read-dispatch loop. The loop has exactly three steps per
iteration: read one `Key` from the keyboard adapter, call
`Application.handle_key(key)`, then drain any cells the user just
submitted and hand each to `Application.execute(cell_id)`.
`asat/keyboard.py` hosts the `PosixKeyboard` and `WindowsKeyboard`
adapters (plus a `ScriptedKeyboard` for tests); the rest of ASAT
never touches terminal input APIs directly.

Three supporting modules round the entry point out:

* `asat/terminal.py` — `TerminalRenderer` subscribes to the bus and
  prints a minimal text trace (startup banner, keystroke echo,
  submitted `$ command` line, output chunks, `[done exit=...]`). The
  audio pipeline is the primary UI; the renderer exists so sighted
  viewers and anyone debugging have a readable mirror of the event
  stream. `--quiet` switches it off.
* `asat/audio_sink.py` — `pick_live_sink()` returns
  `WindowsLiveAudioSink` on Windows (`winsound.PlaySound` with
  `SND_MEMORY | SND_ASYNC`) and `PosixLiveAudioSink` on Linux /
  macOS (pipes WAV blobs to `aplay` / `paplay` / `afplay`). The CLI
  uses the live sink by default on a TTY; `--no-live` opts out,
  `--wav-dir DIR` captures every rendered buffer in parallel.
  `LiveAudioUnavailable` now surfaces only when no player binary is
  installed on POSIX, and the guard message names the binaries to
  install.
* `asat/app.py::Application.build` publishes `SESSION_CREATED` after
  the SoundEngine has subscribed so the startup chime actually plays
  through the sink, and emits `SESSION_LOADED` / `SESSION_SAVED` at
  the matching boundaries.

Open feature requests for the next generation live in
[FEATURE_REQUESTS.md](FEATURE_REQUESTS.md); as of the 2026-04 MVP
stabilization roadmap the remaining gaps are settings-editor record
create / delete (F2), Ctrl+R reverse-incremental command-history
search (F4 deferred leg), tab completion (F23), and the polish
items in F49's code-quality backlog. Hands-on cross-platform smoke
of the four new MVP surfaces is still pending and lives in
[HANDOFF.md](HANDOFF.md).
