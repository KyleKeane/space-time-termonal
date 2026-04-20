# ASAT — Accessible Spatial Audio Terminal

A self-voicing notebook-style terminal **built for blind developers**.
Every state change — a keystroke, a command completion, a menu
highlight, an ANSI event — flows through one synchronous event bus
and is bound to a spoken phrase or a spatialised tone. Keyboard
only, standard library only, no mouse, no screen coordinates.

> **Status.** Pre-1.0 (current `pyproject.toml` says `0.7.0`). The
> four MVP surfaces — live audio on Linux / macOS / Windows, an
> on-screen outline pane, an interactive event-log viewer, and a
> scripted first-run tour — are all usable end-to-end after the
> 2026-04 MVP stabilization roadmap. Remaining gaps are catalogued in
> [docs/FEATURE_REQUESTS.md](docs/FEATURE_REQUESTS.md); a hands-on
> handoff snapshot of what's verified-by-tests vs. pending manual
> smoke lives in [docs/HANDOFF.md](docs/HANDOFF.md).

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

- **Cross-platform live audio on launch.** A pluggable TTS registry
  (`pyttsx3` preferred, `espeak-ng` / macOS `say` / Windows SAPI as
  native fallbacks, tone generator as the deterministic floor)
  combined with a POSIX live sink (`aplay` / `paplay` / `afplay`) +
  the existing Windows `winsound` path means `python -m asat` speaks
  on the first launch on Linux, macOS, and Windows. `:tts list` /
  `:tts use <id>` / `:tts set <param> <value>` swap engines live.
- **An on-screen outline pane.** The renderer subscribes to
  `FOCUS_CHANGED` / `CELL_*` events and paints an indented tree of
  heading cells with a `>` arrow on the focused cell. `]` / `[`
  moves both the audio cursor and the visual marker. `--view
  {trace,outline,both}` picks which panes show on a TTY.
- **An interactive event-log viewer.** `Ctrl+E` opens a narrated
  ring of the last 200 events; Up/Down walks entries, `Enter` jumps
  to the binding's field in the SETTINGS editor, `e` quick-edits the
  `say_template`, and `t` replays the event through the live
  pipeline. Every event is also written to a grouped text log at
  `<workspace>/.asat/log/events-YYYY-MM-DD.log` (or `--log-events
  DIR`).
- **A scripted first-run tour.** A five-beat tour seeds a
  `H1 + H2 + command` demo notebook so the outline pane has
  something to render, narrates the `Ctrl+E` keystroke, announces
  the event-log file path, and lands in INPUT mode. `:welcome`
  replays every beat without re-seeding.
- A **session of editable cells**, each with its own command,
  captured stdout/stderr, and exit code; saved as JSON via
  `--session file.json` and resumable.
- **A persistent shell backend** (POSIX with bash). Every cell flows
  through one long-lived shell, so `cd`, `export`, function defs,
  and shell options carry between cells exactly as they would at a
  real prompt — `cd src` in cell 1, `ls` in cell 2 lists `src/`.
  Pass `--no-shared-shell` to opt out (each cell back to a fresh
  subprocess); Windows still uses per-cell subprocesses today.
- **An asynchronous submission queue.** Submit a cell while an
  earlier one is still running and it lands in a serial background
  queue; the keyboard and action menu stay responsive, and a soft
  tick confirms the submission the instant you hit Enter.
- **Four focus modes** — NOTEBOOK (walk cells), INPUT (type a
  command), OUTPUT (step line-by-line through one cell's captured
  output, with `/` search and `g` goto), SETTINGS (live editor for
  every voice / sound / binding without restarting), plus the
  `EVENT_LOG` overlay introduced by the viewer.
- A **live audio engine**: HRTF spatialisation on stdlib audio,
  three TTS voices, a per-event SoundBank you can edit and save.
- An **F2 actions menu** for context-sensitive copy / navigate
  affordances.
- **Meta-commands** (`:help`, `:state`, `:pwd`, `:save`, `:quit`,
  `:welcome`, `:reset bank`, `:tts …`, `:log`, …) with
  case-insensitive matching and did-you-mean hints on typos.

What is **not** here yet, with the docs grounding the gap:

- No PTY inside a cell, so `vim` / `less` / curses programs do not
  run inside one — see
  [docs/USER_MANUAL.md § What a cell is](docs/USER_MANUAL.md#what-a-cell-is-and-is-not-today).
- No Ctrl+R reverse-incremental command-history search (Up / Down
  recall ships today) — [F4](docs/FEATURE_REQUESTS.md#f4--command-history).
- No settings-editor record create / delete — [F2](docs/FEATURE_REQUESTS.md#f2--settings-editor-create--delete-records).
- No tab completion inside INPUT — [F23](docs/FEATURE_REQUESTS.md#f23--tab-completion).
- Cross-platform smoke on the four new surfaces has only been run
  inside the dev sandbox; see
  [docs/HANDOFF.md](docs/HANDOFF.md) for the outstanding manual
  checks before tagging 1.0.

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
python -m asat                      # live audio on any TTY platform (POSIX + Windows)
python -m asat --wav-dir /tmp/asat  # also capture each rendered buffer to WAV
python -m asat --no-live            # opt out of the live sink (MemorySink only)
python -m asat --view trace         # suppress the outline pane (text trace only)
python -m asat --view outline       # suppress the text trace (outline pane only)
python -m asat --quiet              # suppress the text trace, audio only
python -m asat --check              # run the diagnostic self-test on every covered event, exit
python -m asat --version            # print the version string and exit
```

Per-platform audio prerequisites (the CLI names the missing binary
if none are found; the tone fallback keeps the pipeline moving):

- **Linux:** `pip install pyttsx3` **or** `apt install espeak-ng`,
  plus `alsa-utils` (for `aplay`) or `pulseaudio-utils` (for
  `paplay`).
- **macOS:** ships working out of the box — `say` is built-in and
  `afplay` is on PATH.
- **Windows:** ships working out of the box via SAPI + `winsound`;
  `pip install pyttsx3` gives you extra voices.

**ASAT needs an interactive terminal** — if stdin is not a TTY the
CLI exits with `[asat] cannot start: …` and returns code 2.

### First five minutes

1. Launch with bare `python -m asat`. You hear the session-start
   chime overhead, then the scripted first-run tour: welcome →
   three-cell demo notebook (a H1, a H2, and a pre-filled
   `echo hello, ASAT` cell) → event-log preview → log-path
   announcement → "press Enter to run, or colon h e l p for more".
   The screen paints the outline pane with a `>` on the focused
   cell.
2. Press Enter to run the seeded command. You hear the submit cue,
   the output narrated, and the success chord on exit.
3. Press `Ctrl+E` to open the event log. Up / Down walks entries;
   `Enter` jumps to the binding's field in SETTINGS; `e` quick-edits
   the `say_template`; `t` replays the event with your new phrase.
   `Escape` closes the viewer.
4. Type `:help` + Enter any time for the full cheat sheet. Type
   `:welcome` to replay the tour without re-seeding cells. Type
   `:tts list` / `:tts use espeak-ng` to switch TTS engines live.
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
| `execution_worker.py`      | F62 background queue: serial daemon thread feeding the kernel.            |
| `execution.py`             | `ExecutionRequest` / `ExecutionResult` value types.                       |
| `output_buffer.py`         | `OutputRecorder` + `OutputBuffer`: per-cell line capture from events.     |
| `output_cursor.py`         | OUTPUT-mode line cursor: navigation, search, goto.                        |
| `prompt_context.py`        | Publishes `PROMPT_REFRESH` so re-entering INPUT shows last exit + cwd.    |
| `error_tail.py`            | Auto-narrates the last few stderr lines after a failed command.           |
| `completion_alert.py`      | Watches focus + completion so off-focus completions still alert.          |
| `onboarding.py`            | First-run welcome tour coordinator; sentinel under `ASAT_HOME`; scripted PR 4 beats (tour step, event-log preview, log path, completed). |
| `event_bus.py`             | Synchronous typed event bus; wildcard subscription.                       |
| `events.py`                | `EventType` enum + `Event` dataclass; the contract every module uses.     |
| `jsonl_logger.py`          | `--log` writer: one JSON line per event for diagnostics / replay.         |
| `event_log.py`             | `EventLogViewer`: bounded ring, Ctrl+E overlay, quick-edit + replay.      |
| `event_log_file.py`        | Grouped daily text log under `<workspace>/.asat/log/events-YYYY-MM-DD.log`. |
| `terminal.py`              | Text trace + outline-pane renderer (ANSI-clear redraw on TTY, append-only when piped). |
| `outline.py`               | Pure `render_outline(cells, focus_cell_id, max_width)`; scope + enclosing-heading helpers. |
| `audio.py`                 | Core audio data types (AudioBuffer, sample-rate constants).               |
| `audio_sink.py`            | Sinks: `MemorySink`, `WavFileSink`, `WindowsLiveAudioSink`, `PosixLiveAudioSink` (aplay / paplay / afplay), `pick_live_sink()`. |
| `sound_engine.py`          | Subscribes to events, renders cues + narrations, hands buffers to a sink. |
| `sound_bank.py`            | The bank model: `Voice`, `SoundRecipe`, `EventBinding` records.           |
| `sound_bank_schema.json`   | JSON schema for an on-disk bank file.                                     |
| `sound_generators.py`      | Sine / chord / noise generators consumed by `SoundRecipe.kind`.           |
| `default_bank.py`          | The shipped bank — every default cue, voice, and binding; `COVERED_EVENT_TYPES` enumerates every event that must have a sample payload. |
| `sample_payloads.py`       | Canonical one-per-event-type reference payloads; the source the coverage test and `--check` both consume. |
| `self_check.py`            | Diagnostic self-test behind `--check` — replays every `COVERED_EVENT_TYPE` through the live engine + sink. |
| `tts.py`                   | `TTSEngine` Protocol + adapters: `Pyttsx3Engine`, `EspeakNgEngine`, `SystemSayEngine`, `ToneTTSEngine`. |
| `tts_registry.py`          | Registry of TTS adapters with availability probes; `select_default()` walks the priority list. |
| `hrtf.py`                  | HRTF spatialiser; numpy-accelerated when available.                       |
| `settings_controller.py`   | Mode-aware controller orchestrating the editor + the audio bank; `open_at_binding(binding_id)` used by the event-log viewer. |
| `settings_editor.py`       | The pure editor: cursor over sections / records / fields, undo/redo, `/` search, reset scopes. |
| `help_topics.py`           | The `:help <topic>` registry consumed by the input router.                |
| `ansi.py`                  | ANSI escape parser used by the screen + TUI detector.                     |
| `screen.py`                | `VirtualScreen`: applies ANSI tokens to a 2D grid for menu detection.    |
| `interactive.py`           | TUI menu detection over the virtual screen.                               |
| `tui_bridge.py`            | Glue: streams a child program's bytes through ansi + screen + detector.  |
| `output_playback.py`       | Continuous line-by-line playback of a cell's captured output.             |
| `streaming_monitor.py`     | Gap + beat detection on live output streams.                              |
| `workspace.py`             | Workspace discovery (`.asat/`), multi-notebook layout, `:workspace` meta-commands. |
| `common.py`                | Tiny shared utilities (id generation, UTC clock).                         |

### `docs/` — documentation

| File                       | When to read it                                                           |
|----------------------------|---------------------------------------------------------------------------|
| `USER_MANUAL.md`           | Keystrokes, modes, meta-commands, troubleshooting. Start here as a user.  |
| `CHEAT_SHEET.md`           | Single-page reference: every binding, meta-command, and audio cue.        |
| `SMOKE_TEST.md`            | Hands-on, keystroke-by-keystroke walkthrough with expected narrations.   |
| `ARCHITECTURE.md`          | Module map, focus model, execution path, sync gates, predicate DSL. Read first as a contributor. |
| `DEVELOPER_GUIDE.md`       | Guiding principles, simplicity checklist, the PR recipe.                  |
| `HANDOFF.md`               | MVP verification snapshot: per-surface status, what's test-covered vs. pending manual smoke. Read first if you're picking up the project in a fresh chat. |
| `FEATURE_REQUESTS.md`      | Open gaps (F1 … F64) with code-and-doc pointers; the active roadmap.      |
| `EVENTS.md`                | Every event type and its payload (kept in sync by a test).                |
| `AUDIO.md`                 | Voices, recipes, bindings, HRTF, the spatialiser, TTS registry.           |
| `BINDINGS.md`              | Auto-generated binding reference — regenerate via `python -m asat.tools.render_bindings_doc`. |
| `CLAUDE_CODE_MODES.md`     | Sonification targets for the Claude Code TUI running inside ASAT.         |

### `tests/` — test suite

One file per module (e.g. `test_notebook.py` covers `asat/notebook.py`).
Two cross-cutting suites:

- `tests/test_smoke_scenarios.py` — end-to-end keyboard-and-event
  scenarios mirroring the acts in `docs/SMOKE_TEST.md`.
- `tests/test_user_manual_sync.py`, `tests/test_events_docs_sync.py`,
  `tests/test_default_bank_orphans.py`,
  `tests/test_bindings_introspection.py` (section `BindingsDocInSyncTests`),
  `tests/test_default_bank.py` (class `CoverageTests` — every
  `COVERED_EVENT_TYPE` must have a `SAMPLE_PAYLOADS` entry) — guards
  that fail when the docs drift from the code. See
  [docs/ARCHITECTURE.md § Sync gates](docs/ARCHITECTURE.md#sync-gates)
  for the full list and how to satisfy each one when adding a new
  event type or binding.

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
