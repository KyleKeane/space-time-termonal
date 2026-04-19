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
· F21a settings undo/redo · F6 Windows live audio (POSIX still open).

## Open (roadmap)
F1/F10 cancel + non-blocking exec · F2 settings create/delete · F3
bank reload · F4 command history · F5 SAPI TTS · F6 POSIX live audio
· F7 OSC 133 · F8 measured-HRTF user surface · F12 shell mode +
session CWD · F21b/c settings search + reset · F22 diagnostic log
(`--log path.jsonl`) · F23 tab completion · F24 continuous output
playback · **F25** remappable keybindings · **F26** cell clipboard
(cut/copy/paste cells) · **F27** heading/text cells + outline
navigation + heading-scope selection · **F28** speech output console
(programmatic + braille / screen-reader routing) · **F29** notebook
tabs with per-tab kernel (and child tabs sharing one kernel).

## Non-negotiables
- Pure stdlib. Optional numpy only; no other third-party deps.
- Narration-first: sound before visuals.
- Minimal on-screen chrome: a screen reader already describes text;
  avoid duplicate UI.
- Deterministic tests: `MemorySink` by default, injectable runners /
  clocks, no real audio.
- One feature per PR on branch `claude/accessible-audio-terminal-cOh64`.
- Test command: `python -m unittest discover -s tests -t .` —
  currently **629 passing**.

## Entry points for the next session
- **Roadmap (authoritative):** `docs/FEATURE_REQUESTS.md`
- **User contract:** `docs/USER_MANUAL.md`
- **Architecture tour:** `docs/ARCHITECTURE.md`
- **Audio pipeline:** `docs/AUDIO.md`
- **CLI:** `python -m asat [--live | --wav-dir DIR] [--session PATH] [--quiet]`

## Suggested next PR
**F21b** — `/` search overlay for `SettingsEditor`. Sub-modes mirror
the existing edit sub-mode (`begin_search` / `extend_search` /
`commit_search` / `cancel_search`). Cross-section substring match on
Voice.id / SoundRecipe.id / EventBinding.(id | event_type | voice_id
| sound_id). Park cursor at RECORD level on match. Controller API +
InputRouter `/` binding + tests + docs, then PR.

---

Paste this entire file as the first message of the new session and it
will pick up without further priming.

<!-- MCP PR-creation smoke test: trivial edit to verify Claude Code can open PRs via the GitHub MCP server. -->

