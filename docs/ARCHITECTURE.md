# ASAT Architecture

This document describes how the Accessible Spatial Audio Terminal is
organised. It is the first file a new contributor should read. Every
design choice prioritises blind developers on Windows using a screen
reader: keyboard-only operation, predictable event ordering, and
self-voicing feedback without ever depending on colour or layout.

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
           |  Presentation               |
           |  settings_controller.py     |
           |  settings_editor.py         |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Audio framework            |
           |  sound_engine.py            |
           |  sound_generators.py        |
           |  sound_bank.py              |
           |  default_bank.py            |
           |  audio.py  audio_sink.py    |
           |  tts.py  hrtf.py            |
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Interaction layer          |
           |  input_router.py            |
           |  notebook.py  actions.py    |
           |  output_cursor.py           |
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
           +---------------+-------------+
                           |
           +---------------v-------------+
           |  Data + bus (foundation)    |
           |  cell.py  session.py        |
           |  events.py  event_bus.py    |
           |  output_buffer.py           |
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

Open feature requests for the next generation live in
[FEATURE_REQUESTS.md](FEATURE_REQUESTS.md); the short version is
Windows-native TTS, live-speaker sinks, settings-editor record
creation, and command history.
