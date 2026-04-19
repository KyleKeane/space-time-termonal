# Feature requests

Open gaps in ASAT — things the codebase does not yet do but should, in
roughly the next generation. Each entry explains what is missing, where
the gap surfaces today, and a sketch of what the fix would look like.

This file is descriptive, not prescriptive: priorities and scheduling
live in the PR queue.

---

## F1 — Cancel a running command

**Gap.** There is no INPUT-mode keybinding to cancel a command that
is currently running. The `COMMAND_CANCELLED` event type exists and
the default bank binds a cue to it, but nothing publishes it today.

**Where it surfaces.**
[USER_MANUAL.md](USER_MANUAL.md) explicitly notes: "Cancelling a
running command is a future keyboard binding; for now let commands
finish or send `:quit` to bail out of the whole session."

**Sketch.** Bind `Ctrl+C` in INPUT mode to a `cancel_command`
action. `ExecutionKernel` already owns the subprocess; add a
`cancel(cell_id)` method that terminates it and publishes
`COMMAND_CANCELLED`.

---

## F2 — Settings editor: create / delete records

**Gap.** `SettingsEditor` can edit fields on existing `Voice`,
`SoundRecipe`, and `EventBinding` records, but has no "add new" or
"delete" operations. Users who want a new record today have to edit
the bank JSON on disk and restart.

**Where it surfaces.**
[USER_MANUAL.md](USER_MANUAL.md) troubleshooting — "I want a brand-new
sound" — has to route users out to a file editor.

**Sketch.** Add `create_record(section)` and `delete_record()` to
`SettingsEditor`, surface them as key actions in `SettingsController`
(e.g. `a` = add, `d` = delete, with a confirmation step on delete).
Defaults for a fresh record are obvious (`Voice()` neutral,
`SoundRecipe(kind="silence")`, `EventBinding()` disabled).

---

## F3 — Reload bank from disk

**Gap.** Ctrl+S writes the in-memory bank to disk; there is no inverse
that discards uncommitted edits and reloads the last saved bank.

**Where it surfaces.** A user experimenting with settings can only
undo by remembering every edit they made, or by closing the editor
and reopening it (which does not reload from disk — the session still
holds the live bank).

**Sketch.** Add `SettingsEditor.reload(path)` that calls
`SoundBank.load(path)` and swaps the active bank after a confirmation
prompt. Bind to a keystroke (Ctrl+R) in SETTINGS mode.

---

## F4 — Command history

**Gap.** Up / Down in INPUT mode do nothing. There is no way to recall
or search past commands.

**Where it surfaces.** Every real terminal has this; its absence is
felt the first time a user wants to repeat the previous command.

**Sketch.** Extend `Session` with an ordered list of commands
submitted this session. Bind Up / Down in INPUT mode to walk it;
Ctrl+R opens reverse-incremental search (a small overlay state
machine mirroring the approach `SettingsEditor` already uses).

---

## F5 — Windows-native TTS adapter

**Gap.** The shipping `ToneTTSEngine` is a parametric tone generator
— useful as a self-contained default but not a real voice. On Windows
the obvious target is SAPI / OneCore TTS via `pywin32` (or `ctypes`
to avoid adding a dep).

**Where it surfaces.** [ARCHITECTURE.md](ARCHITECTURE.md) phase
history lists this as a next-generation item.

**Sketch.** Implement the `TTSEngine` protocol against SAPI,
expose a `SapiTTSEngine` class, let the user pick it via the `engine`
field on `Voice`. Add a Voice-level routing step in `SoundEngine`
that picks the right engine by id. Keep `ToneTTSEngine` as the
deterministic fallback for headless tests.

---

## F6 — Live-speaker audio sink (POSIX)

**Status.** Windows live playback shipped (`WindowsLiveAudioSink` in
`asat/audio_sink.py`, selected by `python -m asat --live`). The
remaining gap is POSIX.

**Gap.** On macOS and Linux, `pick_live_sink()` raises
`LiveAudioUnavailable` and the CLI falls back to `MemorySink` with a
message. There is no stdlib-only live audio backend on POSIX — every
candidate (`winsound`) is Windows-only, and `subprocess`-ing `aplay`
/ `afplay` has latency and availability problems.

**Where it surfaces.** `python -m asat --live` on Linux or macOS
prints `[asat] --live unavailable: ...` and continues silently. Users
today have to pair `--wav-dir DIR` with a WAV player, which is
asynchronous and awkward.

**Sketch.** Two reasonable paths:

1. **Stdlib `subprocess` dispatch.** Write each buffer to a temp WAV
   and invoke `/usr/bin/afplay` (macOS) or `aplay`/`paplay` (Linux).
   Simple, no deps, but latency is ~50-150 ms per buffer — noticeable
   for keystroke feedback.
2. **Small C extension via `ctypes`.** Bind to CoreAudio (macOS) or
   ALSA (Linux) directly. Better latency, no new Python deps, but
   non-trivial to build and test.

Either way the implementation goes in `asat/audio_sink.py` behind
the same `AudioSink` protocol, and `pick_live_sink()` learns new
branches. A queueing strategy (drop in-flight keystroke cues when a
narration voice starts) should be designed together with this.

---

## F7 — Default binding for OSC 133 semantic prompt

**Gap.** `ANSI_OSC_RECEIVED` is emitted and
[CLAUDE_CODE_MODES.md](CLAUDE_CODE_MODES.md) points at OSC 133 as a
prompt-boundary signal, but the default bank has no OSC 133 binding.

**Where it surfaces.** Users running shells that emit OSC 133 (zsh
with powerlevel, starship, etc.) get a useful stream of prompt /
command / output markers but hear nothing unless they add a binding.

**Sketch.** Add a predicate-gated `ANSI_OSC_RECEIVED` binding in
`asat/default_bank.py` that matches `body` starting with `133;` and
routes to `system` voice with a short tone on prompt-ready. Test it
with a recorded fixture.

---

## F8 — Measured HRTF loader for end users

**Gap.** `HRTFProfile.from_stereo_wav` exists and the `convolve`
fast-path correctly handles dense kernels, but there is no user
surface for swapping in a measured HRTF. Today it requires editing
Python code.

**Where it surfaces.** [AUDIO.md](AUDIO.md) mentions the loader but
does not document a user path to it.

**Sketch.** Add an `hrtf_path` field to the top-level `SoundBank`
(optional; null → synthetic). `SoundEngine` loads the measured
profile at `set_bank(...)` and uses it for every spatialised cue.
Document the SOFA-to-stereo-WAV conversion recipe in AUDIO.md.

---

## F9 — Root README.md

**Status.** Done — see the repo-root [README.md](../README.md).

---

## F10 — Non-blocking execution + cancel keystroke

**Gap.** `Application.execute(cell_id)` runs the kernel synchronously,
so while a command is running the entry-point loop cannot read keys.
That makes F1 (Ctrl+C cancel) impossible to implement without moving
execution off the main thread.

**Where it surfaces.** Every long-running command — `pytest`, `npm
install`, `git clone` — blocks the whole terminal until it finishes.

**Sketch.** Run the kernel on a worker thread, keep the main loop
draining keys. Use the existing `EventBus` to communicate: the
worker publishes output/completion events the main loop subscribes
to. Add `ExecutionKernel.cancel(cell_id)` that terminates the
subprocess and publishes `COMMAND_CANCELLED`; bind Ctrl+C to it.

---

## F11 — Auto-advance after submit

**Status.** Done — `NotebookCursor.submit()` now appends a fresh
empty cell and enters INPUT on it when the user submits a non-empty
command from the last cell. Empty submits and submits from middle
cells still stay on the submitted cell.

**Gap.** `NotebookCursor.submit()` dropped to NOTEBOOK mode on the
just-run cell. To run another command the user had to press Ctrl+N
(create a fresh cell + enter INPUT). First-time users expect
REPL-like "prompt again after Enter" behaviour instead.

**Where it surfaced.** A user typing `echo first`, Enter, then
`echo second` found that letters were silently swallowed in
NOTEBOOK mode; pressing Enter re-entered INPUT on the previous cell
with its old command pre-loaded and appended to it. Running three
commands in a row produced `echo firstecho secondecho third` in one
cell. The manual's five-minute tour did not mention Ctrl+N.

**Sketch (shipped).** `NotebookCursor.submit()` auto-advances when
(1) the submitted command is non-empty, and (2) the submitted cell
is the last cell in the session. The transition goes straight
INPUT → INPUT so observers see exactly one `[input #…]` banner. See
`asat/notebook.py::NotebookCursor.submit`.

---

## F12 — Shell mode + persistent session CWD

**Gap.** `ExecutionMode.ARGV` is the default, so pipes, redirects,
globbing, `$VAR` expansion, and shell builtins do not work. `cd
/tmp` fails with `No such file or directory: 'cd'` (cd is a
builtin). There is no session-level working directory either, so
even if `cd` launched, subsequent cells would not inherit it.

**Where it surfaces.** `echo a | grep a` outputs `a | grep a`
verbatim. `ls *.py` runs `ls` with a literal `*.py` argument.
Directory navigation is impossible.

**Sketch.** Two parts. (1) CLI-level shell toggle: `--shell` flag
and `:shell on/off` meta-command that flips
`ExecutionKernel._default_mode` to `SHELL`. (2) Session CWD: add a
`cwd: Optional[Path]` field to `Session`, pass it into
`ExecutionKernel.execute`, intercept `cd <path>` as a `:cd` meta-
command (and, under shell mode, capture post-run CWD via a `pwd`
suffix or an explicit shell-keepalive approach). Worth a design
doc before coding: the security trust model of shell mode deserves
its own review.

---

## F13 — In-line buffer editing

**Status.** Done — `FocusState` now carries `cursor_position`, and
the NotebookCursor exposes `cursor_left` / `cursor_right` /
`cursor_home` / `cursor_end` / `delete_forward` / `delete_word_left`
/ `delete_to_start` / `delete_to_end`. INPUT-mode bindings cover
Left / Right / Home / End / Delete plus the readline shortcuts
Ctrl+A / Ctrl+E / Ctrl+W / Ctrl+U / Ctrl+K. Motion is published via
`ACTION_INVOKED`; no new event type was needed.

**Gap.** INPUT mode only accepted printable characters and
Backspace. Left / Right / Home / End / Delete / Ctrl+A / Ctrl+U /
Ctrl+W / Ctrl+K were all unbound. A typo early in a long command
forced the user to backspace the entire line.

**Where it surfaced.** Every command of non-trivial length. Blind
users rely on left/right navigation and word-kill shortcuts to
correct text without re-typing.

**Not shipped (follow-up).** A dedicated SoundBank cue for caret
motion is still pending — observers currently hear the existing
`insert_character` / `backspace` cues but no cue for pure motion.
Audio designers can wire a new binding against the `cursor_left`
etc. action names when ready.

---

## F14 — ActionMenu keystroke binding

**Status.** Done — `Application.build()` now wires a `MemoryClipboard`,
an `ActionCatalog` via `default_actions(...)`, and an `ActionMenu`.
F2 (and Ctrl+. as a fallback) opens the menu from NOTEBOOK, INPUT,
and OUTPUT modes. While the menu is open, Up/Down cycle items,
Enter invokes, and Escape closes. SETTINGS mode is excluded so the
modal editor's keys do not collide.

**Gap.** `asat/actions.py` built a fully-functional `ActionMenu`
with default providers ("copy focused line", "copy all output",
"copy stderr only", "edit command", "explore output", etc.) but
no default keystroke opened the menu, so the whole module was
unreachable from a real user's keyboard. As a direct consequence,
clipboard functionality was also unreachable.

**Where it surfaced.** A user who wanted to copy a single output
line had no way to do it. Tests drove `ActionMenu.activate(...)`
programmatically but no key triggered `ACTION_MENU_OPENED`.

**Sketch (shipped).** `Application.build()` constructs the catalog,
menu, and clipboard; `InputRouter` gains an `action_menu` parameter
and an `open_action_menu` action that snapshots the focused cell
(plus line context in OUTPUT mode) into an `ActionContext` before
calling `menu.open(...)`. See `asat/input_router.py::_open_action_menu`
and `_dispatch_menu_key`.

---

## F15 — Cell delete / move / duplicate from keyboard

**Status: Done.**

**Gap.** `Session.remove_cell(cell_id)` and `Session.move_cell(...)`
exist and are tested, but no keystroke or meta-command reaches
them. Ctrl+N is the only cell-level op bound today, and it only
appends.

**Where it surfaces.** A user who makes a typo in one cell, runs
it, and wants a clean session has to quit and relaunch — there is
no way to delete cells mid-session. Moving a cell to reorganise a
session is equally impossible.

**Sketch (shipped).** `NotebookCursor.delete_focused_cell()`,
`duplicate_focused_cell()`, and `move_focused_cell(delta)` publish
`CELL_REMOVED` / `CELL_CREATED` / `CELL_MOVED`. NOTEBOOK-mode keys
`d` / `y` / Alt+Up / Alt+Down drive them, and `:delete` /
`:duplicate` meta-commands do the same from INPUT mode. `new_cell`
and the F11 auto-advance path now publish `CELL_CREATED` too, so
the default bank's `cell_created` cue actually fires.

---

## F16 — Output search + jump-to-line

**Status: Done.**

**Gap.** OUTPUT mode has no search, no "jump to line N", no
"next/previous error line" navigation. Long outputs are walked one
line at a time with Up/Down only.

**Where it surfaces.** Reading the failing line in `pytest`'s
output means holding Down until you hear "FAILED". A stderr-only
filter is useful but the Action menu's "copy stderr only" is
itself unreachable (F14).

**Sketch (shipped).** `OutputCursor` gained a composer state —
`begin_search` / `begin_goto` + `extend_composer` / `backspace_composer`
/ `commit_composer` / `cancel_composer`. The router binds `/` to
search, `g` to jump-to-line, and `n` / `N` to next / previous
match. Typed characters live-narrow matches and the cursor jumps
to the first hit on every keystroke (relying on the already-firing
`OUTPUT_LINE_FOCUSED` event). Escape restores the line the user
started on; arrow keys are swallowed while a composer is open so
they don't silently dismiss the overlay.

---

## F17 — Richer meta-commands

**Status.** Done (first pass).

**Gap.** Meta-commands are case-sensitive, take no arguments, and
the set is small. `:Help`, `:HELP`, `:help settings`, `:save-as
foo.json`, `:cd /tmp`, `:new`, `:load`, `:pwd`, `:clear` are all
unsupported. A mistyped meta-command (e.g., `:setings`) silently
falls through to the shell and fails.

**Where it surfaces.** A user resuming a session (`python -m asat
--session a.json`) cannot switch to another session without
relaunching. A user who forgets to pass `--session` at launch
cannot persist their work without quitting and relaunching.

**Sketch.** Case-insensitive comparison in `_parse_meta_command`.
Support a single trailing argument: `:cd /tmp`, `:load foo.json`,
`:save-as bar.json`, `:help <topic>`. Add a typo-suggest hint when
a `:xxx` doesn't match any known command ("`:setings` — did you
mean `:settings`? Line ignored."). Add `:pwd`, `:commands`,
`:clear` (reset session), `:new` (start a fresh session without
touching disk).

**Sketch (shipped).** `_parse_meta_command` now returns a
`(canonical, argument, raw_name)` triple and matches the name
case-insensitively via a regex (`:([A-Za-z][A-Za-z0-9_-]*)\s*(.*)$`).
The submit path intercepts every `:xxx` line — known commands
dispatch as before (with the trailing `argument` surfaced on the
ACTION_INVOKED payload), and unknown commands trigger a
HELP_REQUESTED hint with a `difflib.get_close_matches` suggestion
("did you mean `:settings`?"). Two new ambient meta-commands are
now recognised: `:pwd` (announces `os.getcwd()`) and `:commands`
(lists every known meta-command). Session-mutating candidates
(`:cd`, `:load`, `:save-as`, `:clear`, `:new`) are deferred to a
follow-up pass because they need new session-cursor primitives and
cross-cutting file-system handling.

---

## F18 — OS clipboard adapter

**Status.** Done.

**Gap.** `MemoryClipboard` is the only `Clipboard` implementation.
Even if F14 unlocked the menu, "copy output line" would store text
in-process and nothing would land on the system clipboard.

**Where it surfaces.** A user cannot paste an ASAT-copied line
into another application.

**Sketch.** Add a `SystemClipboard` adapter in `asat/actions.py`
(or a new `asat/clipboard.py` if we want platform specialisation).
On Windows use `ctypes` against `user32`/`kernel32`; on macOS
subprocess `pbcopy`; on Linux try `wl-copy` then `xclip`/`xsel`.
Fall back to `MemoryClipboard` with a one-line warning on first
copy. `Application.build` picks the best available adapter unless
tests pass one in explicitly.

**Sketch (shipped).** `SystemClipboard` lives in `asat/actions.py`
alongside `MemoryClipboard`. It holds a priority-ordered list of
command runs per platform (Linux: `wl-copy` → `xclip` → `xsel`;
macOS: `pbcopy`; Windows: `clip`) and invokes them via a
`subprocess.run(..., input=text.encode("utf-8"))` runner. Windows
uses the built-in `clip` tool rather than `ctypes` — it ships with
every supported release and avoids a second code path. The runner
and `sys.platform` are injectable so tests exercise every
fallthrough path without spawning real subprocesses. When every
candidate fails (or the platform has no candidates), the text is
retained in-process and a one-shot `HELP_REQUESTED` event explains
the situation. `Application.build` keeps `MemoryClipboard` as the
in-process default (so tests stay deterministic) and adds a
`clipboard_factory` kwarg; `python -m asat` passes
`SystemClipboard` as the factory so the real CLI gets OS-native
clipboard support automatically.

---

## F19 — Prompt context (exit code, CWD)

**Status.** Done.

**Gap.** The `[input #…]` banner that fires on entering INPUT mode
carries no context about the prior command's exit code, the
current working directory, or the session's git branch. Blind
users re-typing into a fresh cell have no quick auditory cue for
"last thing failed" or "you moved directory".

**Where it surfaces.** After a failure the user hears the failure
chord but, when they start typing again, there is no residual
indicator of the exit code. A user who ran `cd` (post-F12) has no
cue that CWD changed.

**Sketch.** Extend the `FOCUS_CHANGED` payload, or add a new
`PROMPT_REFRESH` event, carrying the trailing context. The
TerminalRenderer prints it; a default binding narrates it briefly
via the `system` voice ("input; last exit 1; /tmp"). Keep it one
compact line so fast typists can skip past it.

**Sketch (shipped).** A new `PROMPT_REFRESH` event carries
`last_exit_code`, `last_cell_id`, `last_timed_out`, and `cwd`. A
new `PromptContext` module (`asat/prompt_context.py`) subscribes to
`COMMAND_COMPLETED` / `COMMAND_FAILED` to remember the trailing
exit code, then publishes `PROMPT_REFRESH` whenever FOCUS_CHANGED
transitions into INPUT mode. The first INPUT transition on a
pristine session (no prior run) is intentionally silent so we
don't spam `[prompt exit=None]` noise. `TerminalRenderer` prints
`[prompt exit=N cwd=…]`; the default sound bank only narrates
non-zero exits via the `system` voice with predicate
`last_exit_code != 0`, keeping the success path quiet. The CWD
provider is injectable (defaults to `os.getcwd`) so tests stay
deterministic.

---

## F20 — First-run onboarding

**Status.** Done.

**Gap.** The very first launch of ASAT on a fresh machine is
indistinguishable from the 100th. The session banner is identical;
nothing walks a newcomer through `--live` vs `--wav-dir` vs
`:help`.

**Where it surfaces.** A user who follows the README's quick start
hears a chime, sees `[input #…]`, and stalls. The existing
`[asat] in-memory sink…` stderr hint is useful but one-line.

**Sketch.** Detect first-run via a sentinel file (e.g.,
`~/.asat/first-run-done`). On first run, queue a longer spoken
tour: "Welcome. Press colon, h, e, l, p, Enter for the keystroke
cheat sheet. Press Escape any time to return to notebook mode."
Skip on subsequent launches and on any run with `--quiet`.

**Sketch (shipped).** A new `FIRST_RUN_DETECTED` event carries
the welcome `lines` and the resolved `sentinel_path`. `OnboardingCoordinator`
(`asat/onboarding.py`) owns the sentinel — by default
`~/.asat/first-run-done`. Its `.run()` method is idempotent: when
the sentinel is missing it publishes the event, creates any
missing parent directories, and writes the sentinel; when the
sentinel already exists it returns `False` without touching the
bus. `Application.build` takes an optional `onboarding_factory`
kwarg and invokes `.run()` *after* `SESSION_CREATED` publishes, so
the greeting lands just after the newcomer knows the session is
alive. The CLI (`python -m asat`) wires the factory automatically,
and gates it on `--quiet` / `--check`. The default SoundBank binds
the event to the narrator voice with a high priority and a short
spoken greeting (the TerminalRenderer prints the full spelled-out
tour). The spelled form (`"h, e, l, p"`) makes TTS pronounce each
character so the user learns the keystroke cadence, not just the
word. A tiny `.reset()` method deletes the sentinel for tests or
re-onboarding scenarios.

---

## F21 — Settings: undo, search, and reset

**Status.** In progress. Undo/redo and `/` search shipped; `:reset`
still pending.

**Gap.** The settings editor has no undo, no search, and no "reset
this field / record / whole bank to defaults". A user who mistypes
a value and commits it has to remember the old value and re-enter it.

**Where it surfaces.** Mis-editing a `voice.rate` to `10.0`
suddenly makes the narrator frantic, and recovering requires
remembering the previous rate. There is no way to find a binding
by `event_type` without walking every record.

**Sketch.** Three sub-features that share infrastructure.
`SettingsEditor.undo()` / `redo()` with a bounded stack of
applied edits. A `/` search overlay at any level, matching the
record's id / event_type / label. A `:reset` meta-command (or
Ctrl+R keystroke) with a confirm to reload the built-in defaults
for the current record, the current section, or the whole bank.
Pairs naturally with F2 (create/delete) and F3 (reload from disk).

**Sketch (shipped — undo/redo).** `SettingsEditor` now maintains
bounded undo/redo stacks (`MAX_HISTORY = 64`). Each successful
`edit()` pushes an `_EditRecord` carrying both the mutated
coordinates (section / record_index / field / old_value /
new_value) and the pre/post bank references; any fresh edit clears
the redo stack (standard Word-style semantics). `undo()` pops the
most recent record, restores the prior bank, parks the cursor on
the field the history step touched, and re-publishes
`SETTINGS_VALUE_EDITED` with old/new reversed so narration and
logs react uniformly. `redo()` is the mirror image. The editor
tracks a `_saved_bank` baseline so the `dirty` flag clears when
undo restores the post-save state and reasserts when redo moves
past it. `SettingsController` exposes `undo()` / `redo()` that
refuse during the edit sub-mode and when the session is closed.
`InputRouter` binds **Ctrl+Z** to `settings_undo` and **Ctrl+Y**
to `settings_redo` inside `FocusMode.SETTINGS`.

**Sketch (shipped — `/` search overlay).** Pressing `/` anywhere
in the settings tree enters a search sub-mode that mirrors the
edit and F16 output-search composers: printable characters extend
the query, Backspace trims, Enter commits, Escape restores the
pre-search cursor (level / section / record / field). Matching is
case-insensitive substring across every section at once — Voice
and SoundRecipe match on `id`; EventBinding matches on `id`,
`event_type`, `voice_id`, or `sound_id` — and the first hit parks
the cursor at **record** level so the narrator reads the matched
record and the user can descend into fields if they choose. After
commit, `n` / `N` cycle forward / backward through the preserved
match list (wrapping). New events `SETTINGS_SEARCH_OPENED`,
`SETTINGS_SEARCH_UPDATED`, and `SETTINGS_SEARCH_CLOSED` narrate
the overlay; the default bank binds them to the system voice so
they narrate cleanly on every platform. `SettingsController`
exposes `begin_search` / `extend_search` / `backspace_search` /
`commit_search` / `cancel_search`; `ascend()`, `undo()`, and
`redo()` all refuse while the search sub-mode is active.
`InputRouter` binds `/` to open, `n` / `N` to cycle, and routes
every key through a dedicated dispatch while searching so motion
keys do not leak into the editor. Reset is the remaining F21
sub-feature.

---

## F22 — Diagnostic log file

**Gap.** There is no way to record a session's events to disk for
later review. A blind user who wants to post-mortem what happened
during a long-running command has only what the audio engine and
text trace produced in real time.

**Where it surfaces.** Debugging an intermittent audio issue, or
sharing a reproduction with a maintainer, requires the user to
remember exactly what they heard.

**Sketch.** `--log path.jsonl` CLI flag attaches a bus subscriber
that writes one JSON line per event. The events already carry
everything needed (`event_type`, `payload`, `source`, `timestamp`).
Post-run, a screen reader or a little pretty-printer can replay
the session.

---

## F23 — Tab completion

**Gap.** Tab in INPUT mode echoes a literal tab into the buffer.
There is no completion of command names, filenames, or history
entries.

**Where it surfaces.** Every command that takes a path argument.
Real terminals have had this for forty years; its absence is the
second-most-felt gap after command history (F4).

**Sketch.** Bind Tab in INPUT mode to a `complete` action.
Implementation: look at the current buffer, detect the "word under
cursor", and offer completions from (a) `$PATH` executables for
the first word, (b) filesystem entries relative to CWD for
subsequent words, (c) `readline`-style cycling on repeated Tab.
Narrate the first candidate on Tab, the next candidate on each
subsequent Tab, and full-accept on any non-Tab key. Needs to
coordinate with F12 (CWD) to be useful.

---

## F24 — Continuous output playback

**Gap.** OUTPUT mode steps line-by-line. There is no "play the
whole captured output end-to-end" mode for catching up on a long
build log.

**Where it surfaces.** A user who ran `pytest` on 2000 tests walks
down 2000 lines one at a time. Current cue design gives no
"continuous" alternative.

**Sketch.** Add a new OUTPUT sub-mode `OUTPUT_PLAYBACK` entered
via `p` or `Space`. While active, the engine auto-advances through
lines at a user-tunable rate (a new `voices.narrator.playback_rate`
field), pausable with any key, cancellable with Escape. Pairs
naturally with F16 (search for starting point) and F19 (stream
context).

---

## F25 — User-remappable keybindings per mode

**Gap.** Every keystroke binding in `asat/input_router.py` is hard-
coded in a `BINDINGS` table that maps `(FocusMode, Key)` to an action
name. Users cannot rebind a single key without editing source. A user
who prefers Ctrl+P / Ctrl+N over Up / Down for history, or who needs
to free Ctrl+W because their terminal emulator swallows it, has no
recourse.

**Where it surfaces.** Any user whose hands know a different editor.
Screen-reader users often have a muscle-memory layout (JAWS, NVDA, or
Orca conventions) that ASAT cannot accommodate today. The manual
describes the bindings as fixed.

**Sketch.** Introduce a keymap file (`~/.asat/keybindings.json`) whose
schema is `{mode: {key_spec: action_name}}`, where `mode` is one of
`notebook`/`input`/`output`/`settings`/`menu` and `key_spec` is the
same `Key.combo(...)` grammar the router already uses internally
(e.g. `"ctrl+p"`, `"alt+up"`, `"escape"`). On startup `InputRouter`
loads the default table, then overlays the user file. A `--keymap
PATH` CLI flag and a `:keymap reload` meta-command drive it. Action
names stay stable as the public contract; conflict detection rejects
duplicate mappings with a clear `HELP_REQUESTED` message. Pairs with
F17 (richer meta-commands) and complements F14 (ActionMenu) so power
users can bind a single key to any menu entry.

---

## F26 — Cell clipboard: cut / copy / paste one or many cells

**Gap.** `Session` can add, remove, move, and duplicate individual
cells (F15), and `MemoryClipboard` / `SystemClipboard` (F18) handle
text, but there is no way to put whole cells on a clipboard and paste
them elsewhere in the notebook — let alone a contiguous range of
cells. Re-organising a notebook requires repeated Alt+Up / Alt+Down
presses or a save-edit-reload round trip.

**Where it surfaces.** A user who prototypes in cells 5-9 and wants
to move that block to the top of the notebook today has to walk nine
moves per cell. Copying a reusable setup block into a second
notebook is impossible.

**Sketch.** Add a notebook-level clipboard slot that holds an
ordered tuple of `Cell` snapshots (command text, output, cell_id
reissued on paste). `NotebookCursor` grows `copy_selection()`,
`cut_selection()`, `paste_after_focus()`. A new anchor + active
cursor pair drives multi-cell selection (Shift+Up / Shift+Down
extends, Escape clears). NOTEBOOK-mode keybindings: `y` copies the
focused cell (or current selection), `x` cuts, `p` pastes after
focus, `P` pastes before. Publish `CELLS_COPIED`, `CELLS_CUT`,
`CELLS_PASTED` so the default bank can narrate (e.g. "copied three
cells"). Cross-notebook paste falls out for free once F29 lands.

---

## F27 — Heading and text cells with hierarchy, navigation, and scope selection

**Gap.** Every cell today is an input / output pair produced by
`ExecutionKernel`. There is no heading cell, no markdown / text
cell, and therefore no notebook outline. Users who want to document
a long exploration have to pile all the narrative into a `#`-prefixed
shell comment and hope it reads well.

**Where it surfaces.** A session that walks through "set up fixtures
→ train a model → evaluate" has no auditory table of contents. A
screen-reader user cannot jump to the next section without Up/Down
through every intermediate cell. The roadmap items from F16 (output
search) and F4 (command history) help navigate *within* content but
not *across* it.

**Sketch.** Three linked pieces:

1. **New cell kinds.** `Cell.kind: Literal["input", "heading",
   "text"]` with `heading_level: int` (1-6) on headings. Rendering
   and TTS narration treat heading cells as announce-only ("heading
   level 2: data preparation") and text cells as prose. Executing a
   heading or text cell is a no-op.
2. **Outline navigation.** NOTEBOOK mode gains `]` / `[` for
   "next / previous heading at the current level or shallower" and
   `}` / `{` for "jump to parent heading". `:outline` prints a full
   tree to the output console for screen-reader review. Publish
   `OUTLINE_NAVIGATED` so the sound bank can cue the level change.
3. **Scope selection.** A heading's scope is every cell between it
   and the next heading of the same level or shallower. A new
   `select_heading_scope()` action selects the focused heading
   and its whole scope — including nested H3/H4 children. Pairs
   with F26's clipboard so "copy this whole section" is a single
   gesture. Scope semantics live in a small `asat/outline.py` with
   a pure function `scope_range(cells, heading_index) -> (start,
   end)` that tests can hit directly.

Inspiration from Wolfram notebook grouping, Jupyter markdown cells,
and VS Code outline view — trimmed to the minimum a blind user
needs, with zero visual chrome: no tree gutters, no collapse
triangles, just spoken structure and keybindings.

---

## F28 — Speech output console (programmatic + braille / screen-reader routing)

**Gap.** When a binding fires with a `say_template`, the rendered
phrase goes straight into `SoundEngine` and out to audio. Nothing
captures the *text* of what is being spoken in a user- or test-
visible buffer. Tests assert on event payloads and synthesis calls
but cannot easily answer "did the narration make sense end to end?".
A braille user has no way to route spoken content to a refreshable
display; a screen-reader user has no way to redirect it to their
own TTS stack instead of ASAT's.

**Where it surfaces.** Authoring a new sound bank today is an
iterate-listen-edit loop. Regression tests on narration quality
(e.g. "the prompt refresh line reads naturally after a failed
command") need an easy textual surrogate. Dual-output users
(braille + audio, or external screen reader + audio) have no hook.

**Sketch.** Add a `SpeechConsole` module that subscribes to the
rendered speech stream. Two integration points:

1. **Capture.** Hook `SoundEngine` (or a narrow adapter around it)
   so every final `say_template` expansion — after predicates fire
   and before TTS synthesis — is published as a new
   `SPEECH_RENDERED` event carrying `{text, voice_id, event_source,
   timestamp}`. `SpeechConsole` keeps a bounded ring of entries
   with programmatic accessors (`entries()`, `clear()`, `tail(n)`)
   and a `tee(callable)` so tests and external routers subscribe
   cleanly.
2. **Routing.** A pluggable `SpeechRouter` protocol sits next to
   `AudioSink` — a `BrailleRouter` stub writes to a line-based
   device, a `ScreenReaderRouter` stub writes to the OS screen
   reader's IPC (SAPI NVDA controller client on Windows, Orca
   dbus on Linux). `--speech-route braille:/dev/ttyUSB0` and
   `--speech-route screen-reader` CLI flags pick a router; default
   stays "audio only". Testing uses an in-memory router that
   records what would have been routed.

Pairs with F5 (Windows TTS adapter) and F22 (diagnostic log) — the
console is effectively a focused, higher-level view of the log
limited to narration events.

---

## F29 — Notebook tabs with per-tab backend kernel (and optional child notebooks sharing one kernel)

**Gap.** `Application` holds exactly one `Session` and one
`ExecutionKernel`. There is no concept of a tab, no way to open a
second notebook without relaunching, and no way to share a running
Python / shell state between notebooks. The CLI is a single-pane
experience.

**Where it surfaces.** A user running a long-training notebook and
wanting a scratch notebook for a quick `ls` has to open a second
terminal window. A user who wants two views into the same kernel
(one for input, one for a long output tail) cannot do it today.

**Sketch.** Two features, one API:

1. **Tabs, one kernel per tab by default.** Introduce a `TabBar`
   at the Application layer — a simple `list[NotebookTab]` with a
   `focus_index`. `Ctrl+T` opens a fresh tab with a fresh
   `ExecutionKernel`; `Ctrl+Shift+T` reopens the last closed tab;
   `Ctrl+Tab` / `Ctrl+Shift+Tab` cycle focus; `Ctrl+W` closes (with
   confirm if dirty). Tabs are announced on focus change: "tab 2 of
   3, notebook 'deploy'". Each tab owns its own `Session`, its own
   `ExecutionKernel`, its own command history (F4), its own
   prompt-context (F19).
2. **Child notebooks sharing a kernel.** A `Ctrl+Shift+N` keystroke
   creates a *child* tab that reuses the currently-focused tab's
   kernel. Visually a second tab, semantically a second notebook
   bound to the same backend — so cell outputs from one can feed
   variable state the other reads. A small `KernelGroup` object
   owns refcounted access so closing the parent tab doesn't strand
   running work in a child.

Draw lessons from Wolfram (a notebook is bound to a kernel), Jupyter
Lab (tabs plus kernel groups), VS Code (Ctrl+\` palette of
terminals), and Windows Terminal (tab bar with per-tab profile) —
but strip the UI to: one line at the top announcing
`[tab N/M: label | kernel-id: XYZ]` on focus change, keyboard-only
navigation, nothing for the screen reader to wade through. Every
visual surface stays single-pane and minimal so navigation cost is
purely in keystrokes, not in spoken chrome.

---

## How to add an entry

Append a section using the template:

```
## F<N> — <short name>

**Gap.** <what the code does not do today>

**Where it surfaces.** <user-visible consequence or doc citation>

**Sketch.** <rough implementation approach>
```

Keep entries small and user-facing. Cross-cutting infrastructure
changes (testing, CI) belong in a separate log.
