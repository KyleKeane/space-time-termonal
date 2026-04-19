# ASAT — Accessible Spatial Audio Terminal

A self-voicing notebook-style terminal **built for blind developers**.
Every state change — a keystroke, a command completion, a menu
highlight, an ANSI event — flows through one synchronous event bus
and is bound to a spoken phrase or a spatialised tone. Keyboard
only, standard library only, no mouse, no screen coordinates.

> **Status.** Pre-1.0 (current `pyproject.toml` says `0.7.0`). The
> Windows audio path, the notebook surface, and the POSIX shared-shell
> backend are usable end-to-end; the POSIX live audio sink and cell
> hierarchy are tracked as feature requests (see "What is not here
> yet" below).

## Who this is for

ASAT is for developers who work without sight on the command line and
want a terminal that *narrates itself* rather than relying on an
external screen reader to scrape the visual output. NVDA / JAWS /
VoiceOver still work alongside ASAT for everything else on the
machine, but inside ASAT the audio comes straight from the app:
spatialised cues for events, three voices (narrator, alert, system)
for spoken text. Sighted contributors get a plain-text trace on
stdout that mirrors what the audio is saying.

## What ASAT actually delivers today

- A **session of editable cells**, each with its own command,
  captured stdout/stderr, and exit code; saved as JSON via
  `--session file.json` and resumable.
- **A persistent shell backend** (POSIX with bash). Every cell flows
  through one long-lived shell, so `cd`, `export`, function defs,
  and shell options carry between cells exactly as they would at a
  real prompt — `cd src` in cell 1, `ls` in cell 2 lists `src/`.
  Pass `--no-shared-shell` to opt out (each cell back to a fresh
  subprocess); Windows still uses per-cell subprocesses today.
- **Four focus modes** — NOTEBOOK (walk cells), INPUT (type a
  command), OUTPUT (step line-by-line through one cell's captured
  output, with `/` search and `g` goto), SETTINGS (live editor for
  every voice / sound / binding without restarting).
- A **live audio engine**: HRTF spatialisation on stdlib audio,
  three TTS voices, a per-event SoundBank you can edit and save.
- An **F2 actions menu** for context-sensitive copy / navigate
  affordances.
- **Meta-commands** (`:help`, `:state`, `:pwd`, `:save`, `:quit`,
  `:welcome`, `:reset bank`, …) with case-insensitive matching and
  did-you-mean hints on typos.
- **First-run onboarding** that introduces the keystroke vocabulary
  the first time you launch on a machine.

What is **not** here yet, with the docs grounding the gap:

- No cell hierarchy / sections / folds —
  [F61](docs/FEATURE_REQUESTS.md#f61--cell-hierarchy-sections-folds-and-grouping).
- No PTY inside a cell, so `vim` / `less` / curses programs do not
  run inside one — see
  [docs/USER_MANUAL.md § What a cell is](docs/USER_MANUAL.md#what-a-cell-is-and-is-not-today).
- No live POSIX speaker sink (Windows works today; POSIX renders
  to WAV files) —
  [F6](docs/FEATURE_REQUESTS.md#f6--live-speaker-audio-sink-posix).

## Install and run

Requires Python 3.10 or newer. No runtime dependencies (`numpy` is
an optional accelerator for measured HRTFs only).

```
git clone https://github.com/KyleKeane/space-time-termonal
cd space-time-termonal
python -m unittest discover -s tests -t .   # confirm the suite passes
```

### Launching a session

```
python -m asat --live               # play audio on the speaker (Windows today)
python -m asat --wav-dir /tmp/asat  # write each rendered buffer to WAV (any platform)
python -m asat --live --wav-dir DIR # Windows: speaker + capture in one run
python -m asat --quiet              # suppress the text trace, audio only
python -m asat --check              # build, print a diagnostic summary, exit
python -m asat --version            # print the version string and exit
```

Bare `python -m asat` runs silently (audio goes to an in-memory
sink) and prints a one-line stderr hint. **ASAT needs an
interactive terminal** — if stdin is not a TTY the CLI exits with
`[asat] cannot start: …` and returns code 2.

### First five minutes

1. Launch with `--live` (Windows) or `--wav-dir /tmp/asat` (POSIX).
   You hear the session-start chime overhead and the trace prints
   `[asat] session <id> ready. Type :help …`.
2. Type `:help` + Enter any time for the cheat sheet (audio + text).
3. Type a command (`echo hi`, `git status`, `python --version`),
   press Enter. Submit cue, output narrated as it streams,
   success or failure chord on exit.
4. Press Escape → NOTEBOOK mode. Up/Down walks cells, Ctrl+O steps
   through the focused cell's output, Ctrl+, opens the live
   settings editor.
5. Type `:quit` + Enter to exit.

The full keystroke cheat sheet lives in
[docs/USER_MANUAL.md](docs/USER_MANUAL.md); a hands-on, narration-by-
narration walkthrough lives in
[docs/SMOKE_TEST.md](docs/SMOKE_TEST.md).

## Repository layout

The codebase is intentionally flat: the domain modules sit directly
under `asat/`, the docs sit directly under `docs/`, and the test
suite mirrors module names one-for-one under `tests/`.

```
space-time-termonal/
├── README.md            ← you are here: project overview + file map
├── LICENSE              ← MIT
├── pyproject.toml       ← package metadata; declares Python ≥ 3.10, no runtime deps
├── asat/                ← the application code (see file table below)
├── docs/                ← user, developer, and architectural docs (see table)
└── tests/               ← unit + scenario tests (one file per module)
```

### `asat/` — application modules

Pipeline order, top-down: a keystroke enters the input router,
becomes an action, mutates state, fires an event, and the audio
engine voices it.

| File                       | What it owns                                                              |
|----------------------------|---------------------------------------------------------------------------|
| `__main__.py`              | `python -m asat` CLI: argument parsing, sink choice, the read loop.       |
| `app.py`                   | `Application.build(...)` — assembles every collaborator into one graph.   |
| `keys.py`                  | The `Key` value object + named constants (UP, ENTER, ESCAPE, …).          |
| `keyboard.py`              | Platform key adapters (raw bytes → `Key`).                                |
| `input_router.py`          | Mode-aware keystroke dispatch table; meta-command parsing; help topics.   |
| `actions.py`               | The F2 / Ctrl+. action menu: catalog, focus state, invocation events.     |
| `notebook.py`              | `NotebookCursor` + `FocusMode`; the source of truth for "where am I?".    |
| `session.py`               | The Session model: ordered cells, save/load JSON, mutation API.           |
| `cell.py`                  | The Cell value object: command, captured outputs, status, exit code.      |
| `kernel.py`                | `ExecutionKernel`: routes a cell to the runner and publishes lifecycle.   |
| `runner.py`                | Thin `subprocess.Popen` wrapper with line-streamed stdout/stderr.         |
| `shell_backend.py`         | F60 persistent shell: one long-lived bash per session via sentinel-framed I/O. |
| `execution.py`             | `ExecutionRequest` / `ExecutionResult` value types.                       |
| `output_buffer.py`         | `OutputRecorder` + `OutputBuffer`: per-cell line capture from events.     |
| `output_cursor.py`         | OUTPUT-mode line cursor: navigation, search, goto.                        |
| `prompt_context.py`        | Publishes `PROMPT_REFRESH` so re-entering INPUT shows last exit + cwd.    |
| `error_tail.py`            | Auto-narrates the last few stderr lines after a failed command.           |
| `completion_alert.py`      | Watches focus + completion so off-focus completions still alert.          |
| `onboarding.py`            | First-run welcome tour; sentinel under `ASAT_HOME`.                       |
| `event_bus.py`             | Synchronous typed event bus; wildcard subscription.                       |
| `events.py`                | `EventType` enum + `Event` dataclass; the contract every module uses.     |
| `jsonl_logger.py`          | `--log` writer: one JSON line per event for diagnostics / replay.         |
| `terminal.py`              | The text trace renderer (`[asat] …`, `[input #…]`, `$ command`, …).       |
| `audio.py`                 | Core audio data types (AudioBuffer, sample-rate constants).               |
| `audio_sink.py`            | Sinks: `MemorySink`, `WinMMSink` (Windows), `WavDirSink` (any platform).  |
| `sound_engine.py`          | Subscribes to events, renders cues + narrations, hands buffers to a sink. |
| `sound_bank.py`            | The bank model: `Voice`, `SoundRecipe`, `EventBinding` records.           |
| `sound_bank_schema.json`   | JSON schema for an on-disk bank file.                                     |
| `sound_generators.py`      | Sine / chord / noise generators consumed by `SoundRecipe.kind`.           |
| `default_bank.py`          | The shipped bank — every default cue, voice, and binding.                 |
| `tts.py`                   | TTS adapters (`pyttsx3` on Windows, `espeak` fallback elsewhere).         |
| `hrtf.py`                  | HRTF spatialiser; numpy-accelerated when available.                       |
| `settings_controller.py`   | Mode-aware controller orchestrating the editor + the audio bank.          |
| `settings_editor.py`       | The pure editor: cursor over sections / records / fields, undo/redo.     |
| `help_topics.py`           | The `:help <topic>` registry consumed by the input router.                |
| `ansi.py`                  | ANSI escape parser used by the screen + TUI detector.                     |
| `screen.py`                | `VirtualScreen`: applies ANSI tokens to a 2D grid for menu detection.    |
| `interactive.py`           | TUI menu detection over the virtual screen.                               |
| `tui_bridge.py`            | Glue: streams a child program's bytes through ansi + screen + detector.  |
| `common.py`                | Tiny shared utilities (id generation, UTC clock).                         |

### `docs/` — documentation

| File                       | When to read it                                                           |
|----------------------------|---------------------------------------------------------------------------|
| `USER_MANUAL.md`           | Keystrokes, modes, meta-commands, troubleshooting. Start here as a user.  |
| `CHEAT_SHEET.md`           | Single-page reference: every binding, meta-command, and audio cue.        |
| `SMOKE_TEST.md`            | Hands-on, keystroke-by-keystroke walkthrough with expected narrations.   |
| `ARCHITECTURE.md`          | Module map, focus model, execution path. Read first as a contributor.     |
| `DEVELOPER_GUIDE.md`       | Guiding principles, simplicity checklist, the PR recipe.                  |
| `FEATURE_REQUESTS.md`      | Open gaps (F1 … F61) with code-and-doc pointers; the active roadmap.      |
| `EVENTS.md`                | Every event type and its payload (kept in sync by a test).                |
| `AUDIO.md`                 | Voices, recipes, bindings, HRTF, the spatialiser.                         |
| `CLAUDE_CODE_MODES.md`     | Sonification targets for the Claude Code TUI running inside ASAT.         |

### `tests/` — test suite

One file per module (e.g. `test_notebook.py` covers `asat/notebook.py`).
Two cross-cutting suites:

- `tests/test_smoke_scenarios.py` — end-to-end keyboard-and-event
  scenarios mirroring the acts in `docs/SMOKE_TEST.md`.
- `tests/test_user_manual_sync.py`, `tests/test_events_docs_sync.py`,
  `tests/test_default_bank_orphans.py` — guards that fail when the
  docs drift from the code.

Run the whole suite with `python -m unittest discover -s tests -t .`.

## Contributing

The fast path: read [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
for guiding principles (core Python only, narration-first, one feature
per PR, documentation lands with the code) and the PR recipe — then
pick an entry from
[docs/FEATURE_REQUESTS.md](docs/FEATURE_REQUESTS.md), which holds every
open gap with a code-and-doc sketch of how to close it. The
architecture overview at
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the right backstop
when a feature touches more than one module.

If you are starting a fresh AI-coding session against this repo, this
README plus the chosen feature-request entry are usually enough
context — both files are kept current on every PR.

## License

MIT. See [LICENSE](LICENSE).
