# ASAT — handoff summary for the next Claude Code session

## What ASAT is
ASAT (Accessible Spatial Audio Terminal) is a command-line terminal
**optimised for a blind user**. Every user-visible event fires a sound
cue or a short TTS narration; the keyboard is the only input; visual
chrome is deliberately minimal so a screen reader does not have to
narrate UI buttons. Pure Python 3.10+ stdlib — numpy is an optional
fast path for measured HRTFs only.

## Design rule of thumb
Before writing a feature, ask: *what does the user hear?* If the
answer is "nothing new" or "more visual clutter," it's wrong.

## Core architecture
- **Event bus** (`asat/event_bus.py`, `asat/events.py`) — every state
  change publishes a typed event. Sounds, narration, renderer, and
  tests all subscribe.
- **Focus modes** — NOTEBOOK / INPUT / OUTPUT / SETTINGS / MENU.
  `asat/input_router.py` maps `(mode, key) → action`.
- **Notebook of cells** (not a linear REPL). `Session` holds `Cell`s;
  `NotebookCursor` is the cursor over cells and characters.
- **SoundBank** (`asat/sound_bank.py`): `Voice` (how), `SoundRecipe`
  (what), `EventBinding` (when). Defaults in `asat/default_bank.py`.
- **Spatial audio** (`asat/hrtf.py`): synthetic HRTF for directional
  cues; measured WAV profiles supported; sparse-impulse fast path for
  synthetic kernels.

## Shipped (F1–F29)
F9 README · F11 auto-advance after submit · F13 in-line buffer editing
· F14 action-menu keystroke · F15 cell delete/move/duplicate · F16
output search + jump-to-line · F17 richer meta-commands · F18 OS
clipboard · F19 prompt context on re-entry · F20 first-run onboarding
· F21a settings undo/redo · F21b settings `/` search overlay · F21c
settings `:reset` / Ctrl+R reset-to-defaults · F36 auto-read stderr
tail on failure.

## Partially shipped
· **F6** Windows live audio shipped; POSIX still open.

## Open (roadmap)
F1/F10 cancel + non-blocking exec · F2 settings create/delete · F3
bank reload · F4 command history · F5 SAPI TTS · F6 POSIX live audio
· F7 OSC 133 · F8 measured-HRTF user surface · F12 shell mode +
session CWD · F22 diagnostic log
(`--log path.jsonl`) · F23 tab completion · F24 continuous output
playback · **F25** remappable keybindings · **F26** cell clipboard
(cut/copy/paste cells) · **F27** heading/text cells + outline
navigation + heading-scope selection · **F28** speech output console
(programmatic + braille / screen-reader routing) · **F29** notebook
tabs with per-tab kernel (and child tabs sharing one kernel) ·
**F30** audio history / repeat last narration · **F31** verbosity
presets · **F32** audio ducking under narration · **F34** completion
alert when focus has moved · **F35** cell bookmarks · **F37**
long-output pacing (silence + progress beats) · **F38** self-voicing
help topics.

## Non-negotiables
- Pure stdlib. Optional numpy only; no other third-party deps.
- Narration-first: sound before visuals.
- Minimal on-screen chrome: a screen reader already describes text;
  avoid duplicate UI.
- Deterministic tests: `MemorySink` by default, injectable runners /
  clocks, no real audio.
- One feature per PR on branch `claude/accessible-audio-terminal-cOh64`.
- Test command: `python -m unittest discover -s tests -t .` —
  currently **760 passing**.

## Entry points for the next session
- **Roadmap (authoritative):** `docs/FEATURE_REQUESTS.md`
- **User contract:** `docs/USER_MANUAL.md`
- **Architecture tour:** `docs/ARCHITECTURE.md`
- **Audio pipeline:** `docs/AUDIO.md`
- **CLI:** `python -m asat [--live | --wav-dir DIR] [--session PATH] [--quiet]`

## Suggested next PR
**F22** — `--log path.jsonl` diagnostic file. A single
`JsonlEventLogger` subscriber that writes every `Event` it sees to
a newline-delimited JSON file (one event per line), wired up through
a new `--log PATH` CLI flag. The file rotates on every session so a
long-running ASAT does not grow an unbounded log. Tests: fixture
that pipes a scripted sequence of events through the logger and
asserts the file round-trips. Doc: a short "Replaying a log" entry
in the user manual plus a payload reference in EVENTS.md. Unlocks
remote debugging of audio issues — the user can attach a log to an
issue and a maintainer can replay it locally into a test harness.

Alternative one-shot PRs if F22 feels heavy: **F4** command history
(ring buffer + Up/Down in INPUT mode); **F23** Tab completion of
executables + paths; **F34** completion alert when focus has moved
(quiet sentinel cue so the user learns a run finished while they
were elsewhere in the notebook); **F3** bank reload (a
`:reload-bank` meta-command plus a file-mtime watcher so hand-edits
to the saved JSON reload live without quitting).

## Maintenance backlog (non-feature cleanups)
Each of these is a small standalone PR. Pick one off the top of the
stack when you want a palate cleanser between features.

- Factor the `if self._settings_controller is None: return` /
  `if self._output_cursor is None: return` guards in
  `asat/input_router.py` (9 sites) and `asat/output_cursor.py` (3
  sites) into a helper or decorator.
- Split `InputRouter._action_handler` (currently an 84-line inline
  dict) into per-subsystem dicts: `_settings_handlers()`,
  `_output_handlers()`, `_menu_handlers()` merged at init.
- Table-drive `SettingsEditor._parse_field_value()`: replace the
  per-section if/elif ladders with a `FIELD_PARSERS` dict keyed by
  `(section, field_name)`.
- Table-drive `InputRouter._handle_meta_command()`: replace the
  if/elif chain with a `_META_HANDLERS: dict[str, Callable]`.
- Replace the `"search"` / `"goto"` magic strings in
  `asat/output_cursor.py` with a `ComposerMode(str, Enum)`.
- Add doc-link comments (single lines) above the state-machine
  transitions in `settings_editor.py` and `output_cursor.py` pointing
  to the relevant `docs/ARCHITECTURE.md` / `docs/USER_MANUAL.md`
  sections, so future readers can orient themselves without reading
  the surrounding code.
- Guard the `_search_position = -1` edge in
  `SettingsEditor._recompute_matches(jump_to_first=False)` — today,
  the `-1` sentinel can persist and make `prev_search_match()` wrap
  to the last match unexpectedly. Tiny fix; add a regression test.

---

Paste this entire file as the first message of the new session and it
will pick up without further priming.

