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

**Status.** Partially shipped. Windows live playback ships
(`WindowsLiveAudioSink` in `asat/audio_sink.py`, selected by
`python -m asat --live`). POSIX (macOS / Linux) is still open —
`pick_live_sink()` raises `LiveAudioUnavailable` on those platforms
and the CLI falls back to `MemorySink` with a spoken warning.

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

**Status.** Shipped. Undo/redo, `/` search, and `:reset` / Ctrl+R
reset-to-defaults are all live.

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
keys do not leak into the editor.

**Sketch (shipped — `:reset` / Ctrl+R reset-to-defaults).** The
editor now takes an optional `defaults_bank` on construction and
exposes a reset confirmation sub-mode at four scopes: `field`,
`record`, `section`, `bank`. `begin_reset(scope)` publishes
`SETTINGS_RESET_OPENED` (with `scope`, `section`, `target_count`,
and — for field/record scope — `record_id` / `field`) so the
default bank narrates "reset foo? press enter to confirm, escape
to cancel". `confirm_reset()` swaps the targeted slice with its
default, pushes one polymorphic `_EditRecord` (scope-aware) onto
the undo stack so Ctrl+Z puts the edits back in one step, and
publishes `SETTINGS_RESET_CLOSED` with an `outcome` of `applied`
or (when the slice already matched defaults) `already_default`.
`cancel_reset()` publishes `outcome="cancelled"`. `_apply_history_cursor`
parks the cursor at the coarsest level that still frames the
change on undo/redo of a wider-scope reset, and `undo`/`redo` only
re-publish `SETTINGS_VALUE_EDITED` for field-scope records so
broader resets don't pretend to be a field edit. `SettingsController`
threads `defaults_bank` through on `open()`, exposes `begin_reset`
/ `confirm_reset` / `cancel_reset` / `resetting` / `reset_scope`,
defaults the scope to the cursor's current level when called with
`None`, refuses while searching, cancels any mid-edit first, and
extends `ascend()` / `undo()` / `redo()` / `begin_edit()` /
`begin_search()` to refuse during the reset sub-mode.
`InputRouter` binds **Ctrl+R** to `settings_reset_begin` inside
SETTINGS mode; while the confirmation is active Enter confirms,
Escape cancels, and every other key is swallowed. From INPUT
mode, `:reset bank` (alias `:reset all`) opens SETTINGS and walks
straight into a bank-level confirmation; any other argument
(`:reset section`, a bare `:reset`, …) surfaces a HELP_REQUESTED
hint that directs the user to Ctrl+R inside SETTINGS so the
cursor gives the reset a specific target. The default bank binds
`SETTINGS_RESET_OPENED` (alert voice + settings chime) and three
single-clause predicate branches on `SETTINGS_RESET_CLOSED`
(`outcome == applied`, `outcome == already_default`, `outcome ==
cancelled`) so the user hears the right feedback every time.

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

## F30 — Audio history / "repeat last narration"

**Gap.** When a narration passes by faster than the user can absorb
it — or a new event speaks over the one they wanted to catch — there
is no way to re-hear the last phrase. Assistive tech on desktops
universally provides a "say-it-again" key; ASAT does not.

**Where it surfaces.** Output-line narration during a noisy `pytest`
run, or quick focus transitions that chain several short cues, where
the user realises "what did that last one say?" too late.

**Sketch.** Add a bounded ring buffer (default 20 entries) inside
`SoundEngine` that records every rendered speech phrase with its
`event_type`, `binding_id`, rendered `text`, and timestamp. Bind
`Ctrl+R` (repeat) to replay the most recent entry via the same voice,
and `Ctrl+Shift+R` to open an "audio history" overlay sub-mode where
Up/Down walks back through the buffer and Enter replays the focused
entry. Emit `NARRATION_REPLAYED` so tests can assert on the buffer
depth and replay ordering. History is purely in-memory; it resets
on process exit.

---

## F31 — Narration verbosity presets

**Gap.** Some users want bare-minimum narration (errors + exit codes
only), others want chatty feedback on every keystroke. Today, the
only way to quiet a class of events is to manually disable bindings
one-by-one in the settings editor.

**Where it surfaces.** A user running a 10-minute build doesn't need
per-line narration but does want the failure cue; today they have
to navigate to each output binding and flip `enabled = false`.

**Sketch.** Add a `verbosity: Literal["minimal", "normal", "verbose"]`
field to each `EventBinding` (default `"normal"`) and a top-level
`SoundBank.verbosity_level` with the same shape. The sound engine
skips any binding whose `verbosity` is stricter than the bank's
current level. Expose a `:verbosity <level>` meta-command and cycle
via `Ctrl+M` inside SETTINGS. Every preset change fires a
`VERBOSITY_CHANGED` event the narrator announces so the user hears
which profile they just entered. Pairs cleanly with F21c reset so
"reset to defaults" can include "and verbosity=normal".

---

## F32 — Audio ducking under active narration

**Gap.** When TTS narration plays while non-speech output cues fire
(output-line chords, keystroke blips), the speech gets masked. There
is no automatic gain management to keep voice intelligible.

**Where it surfaces.** Streaming output during a command: the
per-line sound cues stack on top of the narrator reading a single
line, and the combined mix is hard to parse.

**Sketch.** Add `ducking_enabled: bool` (default `True`) and
`duck_level: float` (0-1, default 0.4) to the top of `SoundBank`.
`SoundEngine` tracks whether a speech buffer is currently mixing; if
so, any concurrent non-speech buffer has its gain multiplied by
`duck_level` before being mixed. The attenuation releases on the
next mix cycle after the speech buffer finishes. Expose both fields
as settings you can live-edit. The implementation lives entirely in
the engine's mixing loop — no events or new subsystems.

---

## F34 — Completion alert when focus has moved

**Gap.** A long-running command that completes in the background
fires the normal completion cue, but if the user has tabbed to a
different window or moved to OUTPUT mode on a different cell, the
cue is easy to miss. Shells historically rang the terminal bell; we
have no equivalent "I'm done, come back" signal.

**Where it surfaces.** `make test` starts on cell 3, user moves to
cell 5 to keep working, `make test` finishes and says "command
completed exit 0" — but only once, at conversational volume, and
the user is already typing into cell 5.

**Sketch.** `ExecutionRunner` already knows the originating
`cell_id` and start time. If `COMMAND_COMPLETED` fires and the
current focus (notebook cursor + mode) has moved away from the
originating cell since `COMMAND_STARTED`, publish an additional
`COMMAND_COMPLETED_AWAY` event. Bind that to a distinctive chime
(louder, wider spatial placement) in the default bank. Two-tier
semantics: the normal completion cue still fires for correctness;
the away-cue is a bonus nudge. Silence it with a binding-level
toggle.

---

## F35 — Cell bookmarks

**Gap.** In a long session the user wants to mark significant cells
("the one where I set up the venv", "the broken test run") and jump
back by name. There's no positional shortcut — a sighted user would
scroll visually, but we don't have that affordance.

**Where it surfaces.** Debugging a multi-step issue where the user
needs to repeatedly revisit the same two or three cells while
interleaving exploration in between.

**Sketch.** Introduce a `BookmarkRegistry` stored on `Session`
mapping user-chosen names to cell ids. Meta-commands: `:bookmark
<name>` captures the focused cell, `:jump <name>` navigates to it,
`:bookmarks` narrates the full list, `:unbookmark <name>` removes
one. Persist the registry in the session JSON so it survives reload.
Emit `BOOKMARK_CREATED` / `BOOKMARK_JUMPED` / `BOOKMARK_REMOVED` for
narration. Tab-completes cleanly once F23 lands.

---

## F36 — Auto-read stderr tail on command failure

**Status.** Shipped.

**Gap.** When a command fails, the user hears the failure chord and
the exit code, but has to manually enter OUTPUT mode and scroll to
find the error text. The single most useful piece of information
(what went wrong) was an extra navigation step away.

**Where it surfaces.** Every failed build, failed test run, failed
`cd`, failed `git pull`. Before F36 the narrator said "command
failed exit 1" — not "command failed: fatal: not a git repository".

**Sketch (shipped).** A new `StderrTailAnnouncer` subscriber
(`asat/error_tail.py`) listens for `COMMAND_FAILED`, fetches the
failed cell's stderr tail from the existing `OutputRecorder`, and
republishes a richer `COMMAND_FAILED_STDERR_TAIL` event carrying
`tail_lines` (list), `tail_text` (newline-joined for templates),
`line_count`, and the propagated `cell_id` / `exit_code` /
`timed_out`. The default bank binds that event to the `alert` voice
with `say_template="{tail_text}"`, so the failure chord + exit-code
narration plays first and the stderr tail follows a beat later.
`Application.build` wires the announcer after `SoundEngine` so the
audio sequence stays in that order.

Tunable: `StderrTailAnnouncer(bus, recorder, tail_lines=N)` at
construction; default is 3. Silent when a failed cell produced no
stderr (the regular failure cue is sufficient). Opt-outable via the
settings editor — flip `command_failed_stderr_tail.enabled = false`
on the default binding to keep only the minimal failure cue.

Ancillary change: the kernel's `_fail_before_launch` now also emits
an `ERROR_CHUNK` for the launch-error message before publishing
`COMMAND_FAILED`, so launch failures (missing executable,
unparseable command string) populate `OutputBuffer` like any normal
stderr and feed both OUTPUT-mode review and the F36 announcer.

---

## F37 — Long-output pacing

**Gap.** A command emitting thousands of lines produces a continuous
narration stream. After thirty seconds the user has lost sense of
progress and the per-line cues become noise. There's no silence
detection ("it's been quiet for 10s — still running?") and no
periodic progress beat during streaming.

**Where it surfaces.** `pytest -v`, `make`, log-tailing. Also:
commands that hang silently have no distinguishing cue from commands
that are just slow.

**Sketch.** A `StreamingMonitor` subscribes to `OUTPUT_CHUNK` and
`COMMAND_STARTED`/`_COMPLETED` for the active cell. It tracks the
time since the last chunk; if the gap crosses `silence_threshold_sec`
(default 5.0) it publishes `OUTPUT_STREAM_PAUSED`, and every
`progress_beat_interval_sec` (default 30.0) while output is
streaming it publishes `OUTPUT_STREAM_BEAT`. Both are opt-in
bindings — the default bank binds them to subtle non-speech cues.
Pairs with F24 (continuous playback): together they give the user a
temporal frame for long-running output.

---

## F38 — Self-voicing help topics

**Gap.** `:help` prints the cheat sheet. A brand-new user — blind,
with no sighted context — needs a spoken tour that explains the mode
model, the key keystrokes, and how to discover things like `Ctrl+,`
for settings. Today the only path is "read the USER_MANUAL in a
screen reader".

**Where it surfaces.** First-run onboarding (F20 shipped) reads a
short welcome but doesn't cover navigation, settings, audio tuning,
or search. A user who wants to learn more has to leave the terminal
to do so.

**Sketch.** Extend `:help` to accept a topic: `:help navigation`,
`:help audio`, `:help settings`, `:help cells`, `:help search`,
`:help topics` (lists the rest). Each topic narrates a 10-20 second
tour via the `system` voice — short enough to absorb, long enough to
be useful. Topics live as plain strings in a new
`asat/help_topics.py` module so they're easy to edit and translate.
`:help` with no argument keeps the current print-the-cheat-sheet
behaviour for users who already know the layout.

---

## F39 — Interactive event log viewer (trigger → jump → edit)

**Gap.** ASAT publishes rich typed events on every state change
(`asat/events.py`), but there is no user-facing surface that shows
the recent event stream, and no path from "I just heard a cue I
want to change" to "I am now editing the binding that produced it".
Today the only way to retune a cue the user disliked is to open
settings, remember the event type, search for the matching
binding, and dive in — a round trip that costs focus every time.

**Where it surfaces.** A blind user hears an unexpected or
too-loud cue during normal work and has no narrated record of
what event fired, which binding picked it, or how to reach that
binding. The foundation — `AUDIO_SPOKEN` events
(`asat/sound_engine.py:192`), wildcard subscribe
(`asat/event_bus.py:24,70`), per-binding ids in the default bank
(`asat/default_bank.py`) — is already in place; nothing composes
it into a viewer.

**Sketch.** New `FocusMode.EVENT_LOG` (`asat/input_router.py`
`FocusMode` enum) backed by an `EventLogViewer` module
(`asat/event_log.py`) that wildcard-subscribes on the bus and
keeps a bounded ring buffer (default 200 entries,
`SoundBank.event_log_capacity` override). Entry path:
`Ctrl+E` from anywhere (ambient, like `Ctrl+,`) plus a `:log`
meta-command (`asat/input_router.py` `_META_HANDLERS`). Focus
lands on the newest entry automatically ("jump to latest"). Each
entry narrates timestamp · event_type · short summary ·
`binding_id` (if one fired).

Four per-entry actions, in priority order:

1. `Enter` — open `SettingsEditor` scoped to the binding that
   fired for that event, cursor parked at field level on
   `say_template` (the thing users most want to tweak). Reuses
   every existing settings sub-mode (search `/`, undo, reset) for
   free because it is a plain `begin_edit` with a pre-set cursor.
2. `e` — inline quick-edit of one of four common fields
   (`say_template` / `voice_id` / `enabled` / `volume`), cycled
   with arrow keys, committed with Enter. Fires the same
   `SETTINGS_VALUE_EDITED` as the full editor so Ctrl+Z still
   walks the change back.
3. `t` — re-trigger: publish a synthetic copy of the selected
   event through the bus so the user hears the change without
   reproducing the original condition.
4. `Escape` — return to the prior focus mode (not SETTINGS — we
   only land there on `Enter`).

**State-machine composition.** `EVENT_LOG` is a peer of
`SETTINGS`, not a child: you cannot be in reset / search / edit
*and* the log viewer simultaneously. Same mutual-exclusion rule
the existing sub-modes already follow. Opening a binding for
edit transitions `EVENT_LOG → SETTINGS`; closing settings returns
to wherever the user entered the log from.

**New events.** `EVENT_LOG_OPENED`, `EVENT_LOG_CLOSED`,
`EVENT_LOG_FOCUSED` (entry_index, event_type, binding_id),
`EVENT_LOG_QUICK_EDIT_COMMITTED` (section, field, old_value,
new_value), `EVENT_LOG_REPLAYED` (original_event_type). Wired
into `asat/events.py::COVERED_EVENT_TYPES`,
`asat/default_bank.py` bindings (system voice for
open/close/focus, narrator voice for the edit-committed payload,
a distinct `alert_replay` cue for re-trigger), and
`tests/test_default_bank.py::SAMPLE_PAYLOADS` for each.

**Suggested PR split.**

- **F39a** — read-only `EventLogViewer` + `FocusMode.EVENT_LOG`
  + `Ctrl+E` / `:log` binding + five new events + default-bank
  bindings + docs. Users can already hear the stream and walk
  it; no edit path yet.
- **F39b** — `Enter` on entry → open `SettingsEditor` at the
  matching binding (field-level cursor on `say_template`).
  Needs a small `SettingsController.open_at_binding(binding_id,
  field="say_template")` helper.
- **F39c** — `e` inline quick-edit sub-mode (mirror the existing
  edit composer in `asat/settings_editor.py`; four-field carousel).
- **F39d** — `t` re-trigger (synthetic event replay).

Documentation touch points: a new "Event log" section in
`docs/USER_MANUAL.md` next to the settings section; a row in the
mode-model table; an entry in the meta-command table for `:log`;
new rows in `docs/EVENTS.md` for the five new event types; a
short subsection in `docs/ARCHITECTURE.md` explaining the
wildcard-subscriber pattern so future viewers (F40) reuse it.

**Why this is a good fit for ASAT.** Narration-first: every
entry is already a spoken line; the viewer just steps through
them. Discoverable: `Ctrl+E` is learnable and `:log` is
typable. And it closes a real loop a blind user can't close
today: *"I heard a cue I didn't like — where do I change it?"*

---

## F40 — Speech viewer (programmatic + on-screen narration log)

**Gap.** `SoundEngine` renders `say_template` phrases and hands
the final text to TTS (`asat/sound_engine.py`), but nothing
captures the resulting string in a user-visible or test-visible
surface. Authoring a new bank is an iterate-listen-edit loop
with no textual playback; tests assert on event payloads but
can't easily answer "did the narration end up readable?".

**Where it surfaces.** Bank authors have no way to proofread
narration without hearing it. Blind users who want a scroll-back
of what was just said (because they missed a word) have no
surface. Distinct from F39 (event log): that one shows
*every* event; this one shows only the *rendered speech stream*.
Distinct from F28 (speech routing): that one is about *where*
speech goes (braille, external screen reader); this is about
*viewing* it.

**Sketch.** A small `SpeechConsole` module
(`asat/speech_console.py`) subscribes to `AUDIO_SPOKEN` (already
published in `asat/sound_engine.py:192`) and keeps a bounded
ring of `{timestamp, voice_id, binding_id, event_source, text}`
entries (default 100). Two surfaces:

1. **Programmatic** — `entries()`, `tail(n)`, `clear()`, plus a
   `tee(callable)` hook for tests and the future F28 routers.
2. **User-facing** — `Ctrl+Shift+E` (or `:speech`) opens a
   read-only viewer very similar to F39's event log but limited
   to spoken lines. Up/Down walks the ring; Enter on a line
   re-reads it; Escape exits.

Pairs with F30 (audio history / repeat last narration) — the
console is the persistent textual form of the same ring. A
single shared buffer inside `SoundEngine` would let both
features share storage.

**New events.** `SPEECH_RENDERED` (fired once the final phrase
is resolved, before TTS synthesis) carrying `{text, voice_id,
binding_id, source_event_type, timestamp}`; add to
`docs/EVENTS.md`, `COVERED_EVENT_TYPES`, and `SAMPLE_PAYLOADS`.
Separate from `AUDIO_SPOKEN` because `AUDIO_SPOKEN` is fired
*after* synthesis; `SPEECH_RENDERED` is fired *before*, so a
router can replace or suppress it.

**Suggested PR split.**

- **F40a** — `SPEECH_RENDERED` event + `SpeechConsole` ring +
  programmatic API + tests.
- **F40b** — `Ctrl+Shift+E` / `:speech` viewer sub-mode.
- **F40c** — share the ring with F30's replay buffer once F30
  lands.

Documentation touch points: new section in
`docs/USER_MANUAL.md` after the event log one; `:speech` row in
the meta-command table; row in the mode-model table for the
viewer sub-mode; new event row in `docs/EVENTS.md`; note in
`docs/AUDIO.md` explaining the `SPEECH_RENDERED` vs
`AUDIO_SPOKEN` timing distinction.

---

## F41 — First-run silent-sink guard

**Status: Shipped.**

**Gap (at time of shipping).** On POSIX the default CLI path
without `--live` used `MemorySink`, so first-run onboarding
narrated the welcome into a buffer that was never played. A
brand-new user heard silence and reasonably concluded ASAT was
broken.

**Sketch (shipped).** `OnboardingCoordinator.__init__`
(`asat/onboarding.py:50`) gained `has_live_audio: bool = True`
and `hint_stream: Optional[IO[str]] = None` kwargs. When both
`is_first_run()` and `not has_live_audio` are true, `run()`
prints `SILENT_SINK_HINT` (a plain-text line naming `--live`,
`--wav-dir DIR`, and `--check`) to `hint_stream` (defaults to
`sys.stderr`) **before** publishing `FIRST_RUN_DETECTED`. Gated
on first-run only so the hint never repeats. `asat/__main__.py`
computes `has_live_audio = bool(args.live) or args.wav_dir is
not None` and plumbs it through `_onboarding_factory` so the
existing CLI hint stays cooperative rather than duplicative.
Tests: three new cases in `tests/test_onboarding.py`
(`test_silent_sink_writes_hint_before_publishing`,
`test_live_audio_suppresses_silent_sink_hint`,
`test_silent_sink_hint_is_first_run_only`). Future callers
(F44 `:welcome` replay) inherit the behaviour for free.

---

## F42 — Full `--check` diagnostic self-test

**Gap.** `python -m asat --check` exists
(`asat/__main__.py:79`) but is currently a short-circuit that
prints a version line and exits. It is not a real self-test: it
does not play a cue per voice, it does not verify the default
bank validates, and it does not confirm live audio actually
reaches the speaker. A blind user setting up ASAT on a new
machine has no single command that answers "does this install
work?".

**Where it surfaces.** First-launch troubleshooting on any
platform. Compounds with F6 (POSIX live audio) and F41 (silent
sink) — today the user finds out "no audio" only by running a
real command and hearing nothing.

**Sketch.** Expand `--check` into a four-step self-test that
prints a PASS/FAIL per step and narrates the same on the
selected sink:

1. **Bank validates.** `default_sound_bank().validate()` — fast,
   no I/O.
2. **Every voice speaks.** For each voice in the default bank,
   render a short canned phrase ("voice foo check") through the
   real TTS engine and route it to the active sink. Catches
   SAPI / engine availability bugs.
3. **One cue per covered event.** Publish one `SAMPLE_PAYLOADS`
   event per `COVERED_EVENT_TYPES` member (same data
   `tests/test_default_bank.py:17` uses), confirm at least one
   buffer lands on the sink.
4. **Live playback reachable.** If `--live` is set, confirm
   `pick_live_sink()` returned a real backend; otherwise report
   `MemorySink` explicitly so the user knows.

Implementation lives in `asat/self_check.py` with an entry point
`run_self_check(bank, sink, *, stdout=sys.stdout) -> int` that
returns the exit code. `asat/__main__.py` wires it to the
`--check` flag. Emit `SELF_CHECK_STEP` events so the test suite
and future diagnostic log (F22) can record the run.

**Documentation touch points.** New "Diagnosing audio issues"
subsection in `docs/USER_MANUAL.md`; mention in `README.md`
quick-start; cross-reference from F41's silent-sink hint.

---

## F43 — Guided first-command tour

**Gap.** F20 (shipped) narrates a welcome and explains the key
meta-commands, but stops there. The user is then dropped into an
empty notebook with no prompt to actually run anything, so the
first-run experience ends on a passive "press colon h e l p" —
the user still has to invent their own first command to hear what
`COMMAND_SUBMITTED` / `COMMAND_STARTED` / `COMMAND_COMPLETED` /
exit-code narration sound like.

**Where it surfaces.** A brand-new user who has just finished
onboarding knows how to invoke `:help` but has not yet heard any
of the execution cues that define ASAT's identity. They often
spend their first minute guessing what to type.

**Sketch.** After `OnboardingCoordinator.run()`
(`asat/onboarding.py`) publishes `FIRST_RUN_DETECTED` and
commits the sentinel, queue one follow-up step: pre-populate the
first cell with `echo hello, ASAT` and narrate *"Press Enter to
run your first command. Press Escape to clear the line and type
your own."* Fire a new `FIRST_RUN_TOUR_STEP` event the default
bank binds to the narrator. The user hears the full
submit→start→complete arc on a known-good command, learns the
cues by association, and can replace the placeholder at will.

Cleanly opt-out-able by the same `--quiet` / `--check` /
sentinel flags as F20. If the user overwrites or clears the
pre-filled line before pressing Enter, the tour step is
considered complete either way — we do not re-insert.

**Documentation touch points.** Extend
`docs/USER_MANUAL.md`'s "Five-minute tour" so the first
paragraph matches what a fresh install actually does now;
mention the tour in `README.md` quick-start.

---

## F44 — `:welcome` meta-command to replay onboarding

**Gap.** Once the first-run sentinel at `~/.asat/first-run-done`
(`asat/__main__.py:232`) is written, there is no supported path
to re-hear the onboarding narration. A user who missed a word,
wants to re-learn after a long hiatus, or wants to demo ASAT to
someone else has to manually delete the sentinel and relaunch.

**Where it surfaces.** Support and teaching — every time
someone says "wait, what were those key bindings again?" the
only answer today is `rm ~/.asat/first-run-done && python -m asat`.

**Sketch.** Add a `:welcome` meta-command
(`asat/input_router.py` `_META_HANDLERS`) that re-invokes
`OnboardingCoordinator.run(force=True)` on the live bus. The
coordinator grows a `force: bool = False` parameter that skips
the sentinel check but does *not* rewrite the sentinel (the
sentinel's meaning stays "the user has seen this once"). Accepts
an optional argument: `:welcome tour` runs the F43 guided
first-command tour as well; bare `:welcome` replays just the
spoken welcome.

Pairs with F38 (self-voicing help topics) — `:welcome` is the
one fixed-script tour; `:help <topic>` covers everything else.

**Documentation touch points.** New row in the meta-command
table at `docs/USER_MANUAL.md:199-208`; mention in the
"Your first launch" subsection so users know they can re-hear
it; cross-reference from F38.

---

## F45 — `ASAT_HOME` environment variable for portable installs

**Status.** Shipped (minimal form — the env var and the single
sentinel call-site).

**Gap.** Every user-owned file path was hard-coded to
`Path.home() / ".asat" / <filename>`. The onboarding sentinel
at `asat/__main__.py:232`, the implicit future home for F4
(command history), F25 (`~/.asat/keybindings.json`), F35
(persistent bookmarks), and any other per-user state were all
anchored to a single process-wide directory the user could not
redirect. A tester running multiple ASAT installs side-by-side,
a CI job running the CLI, and a shared-workstation user all had
no clean isolation.

**Where it surfaced.** Power-user isolation; CI environments;
multi-install testing. Most acutely: F46 — the CLI test suite
was writing the first-run sentinel into the developer's real
home directory on every run.

**Sketch (shipped — minimal form).** A small private helper
`_asat_home() -> Path` in `asat/__main__.py` returns
`Path(os.environ["ASAT_HOME"])` when the env var is set (and
non-empty), otherwise `Path.home() / ".asat"`. Exactly one
call-site was migrated: the onboarding factory's sentinel path.
That single change unlocks clean test isolation (F46) and lets
portable-install users redirect onboarding state to any
directory they own. Documented in
`docs/USER_MANUAL.md` under "Environment variables".

**Follow-up (not shipped).** Promoting the helper to a dedicated
`asat/user_paths.py` module and migrating every future
per-user path (F4 command history, F25 keymap file, F35
bookmarks) through it is deferred until one of those features
lands and needs the second call-site. Until then the one-file
helper is simpler and honours the "flat when possible"
principle.

---

## F46 — Onboarding sentinel test isolation (critical bug)

**Status.** Shipped.

**Gap.** `asat/__main__.py` hard-coded the first-run sentinel at
`Path.home() / ".asat" / "first-run-done"`, and
`tests/test_cli.py` called `cli.main([...])` without patching
`_onboarding_factory`. `test_cli.py` patched `pick_default` only,
so a real sentinel landed in the developer's home directory on
every fresh-suite run. Verified directly: `~/.asat/first-run-done`
existed on this machine with an mtime matching the last test run.

**Where it surfaced.** Any developer running
`python -m unittest discover -s tests -t .` silently lost first-run
onboarding on their own install. CI runs wrote the sentinel into
`$HOME` of the runner. Shared-machine workflows were actively
harmed.

**Sketch (shipped).** Stacks on F45. Two changes:

1. **`_onboarding_factory` consults `_asat_home()`** — F45's tiny
   helper — for the sentinel directory. Production behaviour
   is unchanged (default remains `~/.asat/first-run-done`);
   the env var provides the override point.
2. **`tests/test_cli.py` gains `_AsatHomeIsolated`** — a base
   class whose `setUp` creates a tempdir, points `ASAT_HOME` at
   it, and tears both down on exit. Every existing CLI test
   class now inherits from it, so no test can accidentally
   reintroduce the bug by calling `cli.main` without suppressing
   onboarding.

Two regression tests accompany the fix:

- `AsatHomeHelperTests` covers the three branches of
  `_asat_home()` (unset env var, explicit override, empty
  string).
- `SentinelLocationTests.test_first_run_sentinel_lands_in_asat_home_not_real_home`
  runs `cli.main([])` end-to-end and asserts the sentinel lands
  under the tempdir. Its failure message names F46 so a future
  regression is self-describing.

Full suite: 760 → 764 passing.

**Documentation.** Header comment at the top of
`tests/test_cli.py` spells the isolation contract out for future
contributors: any new CLI test must inherit from
`_AsatHomeIsolated`. `docs/USER_MANUAL.md` "Environment variables"
subsection documents the user-visible half.

---

## F47 — Package version + pyproject metadata hygiene

**Status: Shipped.**

**Gap (at time of shipping).** `pyproject.toml` declared
`version = "0.6.0"` while `asat/__init__.py:191` declared
`__version__ = "0.7.0"`, and the `readme` field still carried
the Phase-1-era inline placeholder `{ text = "Phase 1
foundation: data models and event bus." }` instead of pointing
at the real `README.md`.

**Sketch (shipped).** `pyproject.toml` now declares
`version = "0.7.0"` and `readme = "README.md"` (markdown content
type is inferred from the `.md` extension). New
`tests/test_metadata.py` reads `pyproject.toml` via `tomllib`
and asserts three invariants so the pair cannot drift again:
versions match, `readme` points at `README.md`, and the
description is not the Phase-1 placeholder.

---

## F48 — Discoverability: `:reset` docs row + SETTINGS HELP_LINES

**Status: Shipped.**

**Gap (at time of shipping).** F21c shipped `:reset bank` /
`:reset all` and SETTINGS Ctrl+R / Ctrl+Z / Ctrl+Y, but two
discoverability surfaces missed the update: the primary
meta-command table in `docs/USER_MANUAL.md` did not list
`:reset bank`, and `HELP_LINES` in `asat/input_router.py` did
not name the SETTINGS-mode undo/redo keystrokes at all. Users
who learn ASAT only from `:help` had no audible path to them.

**Sketch (shipped).** Added `:reset bank` row to the meta-command
table at `docs/USER_MANUAL.md`. Extended `HELP_LINES` with a
new SETTINGS line: `"Ctrl+Z undo, Ctrl+Y redo edits in the order
you made them."` (slotted before the existing Ctrl+R reset
line). New regression test
`tests/test_input_router.py::SettingsResetBindingTests::test_help_mentions_settings_undo_redo`
names F48 in its failure messages so a future edit cannot
silently regress discoverability.

---

## F49 — Code-quality hygiene backlog

**Gap.** A handful of small refactor and regression-guard items
surfaced during the repo audit. None is blocking; each is short
enough to serve as a palate cleanser between larger features.
Kept grouped under one entry so the top of the backlog stays
focused on user-visible features, not code churn.

**Where it surfaces.** Mostly readability and future-author
confusion. Each bullet names the exact file and the concrete
smell so an implementer can land a tiny, reviewable PR without
re-discovering the problem.

**Sketch.** Each bullet below is a self-contained standalone PR.
Pick any one when you want a short refactor loop.

- **Factor repeated `None` guards.** `asat/input_router.py` has
  9 sites of `if self._settings_controller is None: return` and
  3 matching sites in `asat/output_cursor.py` for
  `self._output_cursor`. Extract a small decorator or helper
  method (`_require_controller`). Tiny; clarifies every affected
  method.
- **Split `InputRouter._action_handler` dispatch.** Today it is
  one 84-line inline dict. Split into per-subsystem dicts
  (`_settings_handlers()`, `_output_handlers()`,
  `_menu_handlers()`) and merge at init. No behaviour change;
  one-screen-per-subsystem reading order.
- **Table-drive `SettingsEditor._parse_field_value()`.** Replace
  the per-section if/elif ladders with a `FIELD_PARSERS` dict
  keyed by `(section, field_name)`. Adding a new field type
  becomes one dict entry instead of a new branch.
- **Table-drive `InputRouter._handle_meta_command()`.** Replace
  the if/elif chain with `_META_HANDLERS: dict[str, Callable]`.
  Already partially shaped that way since F17; finish the job.
- **Name the composer modes.** Replace the `"search"` / `"goto"`
  magic strings in `asat/output_cursor.py` with a
  `ComposerMode(str, Enum)`. Catches typos at definition time.
- **Doc-link comments on state-machine transitions.** One-line
  pointers above each `focus_mode`/sub-mode transition in
  `asat/settings_editor.py` and `asat/output_cursor.py` linking
  to the relevant section of
  [`ARCHITECTURE.md`](ARCHITECTURE.md) or
  [`USER_MANUAL.md`](USER_MANUAL.md). Helps a future reader
  orient in under a minute.
- **Regression-guard the `-1` search sentinel.** In
  `SettingsEditor._recompute_matches(jump_to_first=False)` the
  `_search_position = -1` sentinel can persist and make
  `prev_search_match()` wrap to the last match unexpectedly.
  Tiny fix; add a regression test that names F49 in the failure
  message so future regressions are self-describing.

**Documentation touch points.** Each bullet updates at most one
docstring in the touched module; no user-manual changes; the
`HANDOFF.md` test count moves forward by whatever new regression
tests the bullet adds.

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
