# ASAT handoff snapshot (2026-04-20)

This file is the first thing a fresh AI-coding session (or a new
human contributor) should read when picking up ASAT. It records
**what was shipped in the 2026-04 MVP stabilization roadmap, what
is verified by the test suite, and what still needs hands-on smoke
before 1.0 can be tagged.**

[`README.md`](../README.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md)
are the two other files a new contributor should read after this
one; [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) is the PR recipe;
[`FEATURE_REQUESTS.md`](FEATURE_REQUESTS.md) is the remaining
roadmap.

## TL;DR

- **1295 tests pass** on `python -m unittest discover -s tests -t .`
  as of the last commit on `main`.
- **Four MVP surfaces** — live audio, outline pane, interactive
  event log, scripted first-run tour — are wired and covered by
  unit + integration tests, but the cross-platform hands-on smoke
  below is **not** yet complete.
- **Do not start PR 5.** Take the outstanding smoke list to your
  shell first; any follow-up PR should be scoped from what that
  smoke reveals, not from speculation.

## What the 2026-04 roadmap delivered

Each PR flipped one observable end-user behaviour at launch. The
plan that scoped them is archived at
`/root/.claude/plans/scalable-seeking-forest.md` in the session
host; this section is the authoritative summary on disk.

### PR 1 — live audio on every platform (merged #??)

- New `asat/tts_registry.py` with `Pyttsx3Engine`,
  `EspeakNgEngine`, `SystemSayEngine`, `ToneTTSEngine` adapters
  behind the existing `TTSEngine` Protocol.
- `PosixLiveAudioSink` added to `asat/audio_sink.py` — pipes WAV
  blobs to `aplay` / `paplay` / `afplay`. `pick_live_sink()`
  returns it on Linux / macOS.
- New meta-commands: `:tts list`, `:tts use <id>`, `:tts set
  <param> <value>`.
- `--live` is default on a TTY; F41 silent-sink guard re-phrases to
  name the player binaries to install.

**Closes:** F5 (Windows-native TTS / cross-platform), F6
(live-speaker sink POSIX), F28 (pluggable engine) — each marked
with a status block + follow-up notes in `FEATURE_REQUESTS.md`.

### PR 2 — on-screen outline pane (merged #93)

- `asat/outline.py` exposes a pure `render_outline(cells,
  focus_cell_id, max_width) -> list[str]`.
- `asat/terminal.TerminalRenderer` now subscribes to `FOCUS_CHANGED`
  / `CELL_*` / outline-fold events and repaints the pane; ANSI-clear
  redraw on TTY, append-only trace when piped.
- `--view {trace,outline,both}` CLI flag, default `both` on TTY,
  `trace` under `--quiet` or a non-TTY.
- `]` / `[` moves both the audio cursor and the `>` visual marker.

**Closes:** F27 render leg, F40 (on-screen narration log — the
outline is the narrated tree), F61 (cell hierarchy render).

### PR 3 — interactive event-log viewer + grouped file log (merged #94)

- `asat/event_log.py` — `EventLogViewer` holds a bounded ring of
  200 entries, narrator-formatted strings, `focus_latest` /
  `focus_previous` / `focus_next`, quick-edit sub-mode cycling
  `say_template` / `voice_id` / `enabled` / `volume`, replay
  helper.
- `asat/event_log_file.py` — wildcard subscriber writing
  `<workspace>/.asat/log/events-YYYY-MM-DD.log` grouped by
  `KEY_PRESSED`. Auto-enabled with a workspace; otherwise
  `--log-events DIR`.
- `FocusMode.EVENT_LOG` added; `Ctrl+E`, `:log`, `:log tail`,
  Up / Down / Enter / Escape / `e` / `t` wired.
- `SettingsController.open_at_binding(binding_id)` jumps to the
  binding's field.
- Six new event types: `EVENT_LOG_OPENED`, `EVENT_LOG_CLOSED`,
  `EVENT_LOG_FOCUSED`, `EVENT_LOG_QUICK_EDIT_COMMITTED`,
  `EVENT_LOG_REPLAYED`.

**Closes:** F39 (trigger → jump → edit → replay), F63 (grouped
text log).

### PR 4 — scripted first-run tour (merged #95)

- `asat/onboarding.OnboardingCoordinator` gained four new beat
  publishers (`publish_tour_step`, `publish_event_log_preview_beat`,
  `publish_log_path_beat`, `publish_tour_completed_beat`), each
  carrying a `replay: bool` payload marker.
- `asat/app.Application.build` seeds a `H1 + H2 + command` demo
  notebook on first run, then calls `_run_scripted_tour
  (replay=False)`.
- `:welcome` replays every beat with `replay=True` and does **not**
  re-seed cells.
- Three new event types: `FIRST_RUN_TOUR_EVENT_LOG_PREVIEW`,
  `FIRST_RUN_TOUR_LOG_PATH`, `FIRST_RUN_TOUR_COMPLETED`.
- Log-path announcement gated by `predicate="path != ''"` so the
  narration never fires with an empty path.

**Closes:** F43 follow-through, F44 (`:welcome` replay parity).

## Per-surface verification state

| Surface                    | Code on `main` | Unit tests   | Integration in `test_app.py` | Hands-on smoke pending |
|----------------------------|----------------|--------------|-------------------------------|------------------------|
| Live audio (POSIX + Win)   | yes            | yes          | yes                           | Linux + macOS + Win    |
| Outline pane renderer      | yes            | yes          | yes                           | real TTY repaint       |
| Event-log viewer + file    | yes            | yes          | yes                           | edit + replay loop     |
| Scripted first-run tour    | yes            | yes          | yes                           | fresh-VM sentinel wipe |

The test suite proves every surface works **in the dev sandbox's
headless fake terminal + `MemorySink`**. None of it proves audio
comes out of real speakers on a real user's machine.

## Outstanding manual smoke (before tagging 1.0)

1. **Fresh-VM install, Linux.** Spin up a clean Ubuntu or Fedora
   VM, `pip install pyttsx3`, `apt install espeak-ng alsa-utils`,
   remove `~/.asat/first-run-done`, run `python -m asat`. Confirm:
   (a) audible welcome chime, (b) spoken tour, (c) visible outline
   pane, (d) `Ctrl+E` opens the viewer and narrates entries,
   (e) `:quit` exits cleanly. Same VM: `python -m asat --check`
   must exit 0.
2. **Fresh-VM install, macOS.** Same flow on a stock macOS box;
   `say` and `afplay` ship by default, so no extra install. Confirm
   items (a)-(e).
3. **Fresh install, Windows.** On a stock Windows host with SAPI,
   run `python -m asat`. Confirm items (a)-(e); `winsound` should
   still drive the live sink.
4. **TTS engine swap.** On each platform: `:tts list` speaks
   available engines; `:tts use <id>` switches live; the next
   narration uses the new voice; `:tts set rate 200` changes speed
   without restart.
5. **Edit-in-place loop.** `echo hi`, `Ctrl+E`, `Up` to the
   `command.completed` entry, `Enter` → SETTINGS opens at that
   binding, `e` edits `say_template`, `Enter` commits, `Escape`
   back to viewer, `t` replays — the new phrase must be heard.
6. **Event log file.** With a workspace open, `tail -f
   <workspace>/.asat/log/events-*.log` must grow as events fire,
   grouped by `key_pressed`.

When each check above has been run and documented, update
`FEATURE_REQUESTS.md` to mark the feature "verified on <platform>
<date>" and re-generate `docs/BINDINGS.md` if any binding text
changed.

## Gotchas a fresh chat will not know

- **Predicate DSL is not Python.** `EventBinding.predicate` is the
  DSL documented in [ARCHITECTURE.md § Predicate DSL](ARCHITECTURE.md#predicate-dsl);
  `key op literal` only, no attribute access, no `and` / `or`. The
  default binding for `FIRST_RUN_TOUR_LOG_PATH` gates on
  `predicate="path != ''"` for exactly this reason — an earlier
  attempt at `payload.path` silently dropped every event.
- **Sync gates cascade.** Adding a single `EventType` member
  requires: (1) a `SAMPLE_PAYLOADS` entry in
  `asat/sample_payloads.py`, (2) a row in `docs/EVENTS.md`, (3) a
  binding in `asat/default_bank.py` if it's a user-audible event,
  (4) regenerating `docs/BINDINGS.md`. Missing any one produces a
  test failure in a file that does not mention your event; see
  [ARCHITECTURE.md § Sync gates](ARCHITECTURE.md#sync-gates) for
  the full table.
- **`_replay_welcome` must not re-seed cells.** `:welcome` is idempotent
  by design — the user's notebook must survive. Only the first-run
  code path in `Application.build` owns cell seeding; tests pin
  this behaviour (`test_welcome_meta_command_replays_every_scripted
  _beat`).
- **`pick_live_sink()` raises on POSIX only if no player is
  installed.** The hosts that ship `aplay` / `paplay` /
  `afplay` unconditionally are most distros + macOS; a Docker
  `python:3.12-slim` image has none. `--no-live` on such hosts is
  legitimate, not a bug.
- **Git / PR workflow in this repo.** The user creates and merges
  PRs manually from the pushed branch; the assistant pushes and
  provides a PR-creation URL. MCP GitHub integration has been
  flaky in recent sessions. Follow the branch-naming precedent on
  `main` (e.g. `claude/mvp-pr5-…` for the next focused PR).

## Where to pick up next

The two natural follow-ups once the smoke list is clean:

- **F2 — Settings editor: create / delete records.** The editor
  can modify existing records but not add or remove them. See the
  sketch in `FEATURE_REQUESTS.md § F2`.
- **F4 deferred leg — Ctrl+R reverse-incremental history search.**
  Up / Down recall shipped; reverse-incremental needs a composer
  overlay analogous to the SETTINGS `/` search.

Neither is blocking 1.0; both are the right size for a fresh-chat
PR.
