# Feature requests

Open gaps in ASAT — things the codebase does not yet do but should, in
roughly the next generation. Each entry explains what is missing, where
the gap surfaces today, and a sketch of what the fix would look like.

This file is descriptive, not prescriptive: priorities and scheduling
live in the PR queue.

---

## F1 — Cancel a running command

**Status: Shipped.** **Ctrl+C** in INPUT mode cancels the running
cell via a new `cancel_command` action; `ExecutionKernel.cancel(cell_id)`
signals the active runner (`ProcessRunner.terminate()` →
SIGTERM/TerminateProcess; `ShellBackend.cancel()` → SIGINT via killpg
to the shell's foreground child while the shell itself stays alive
through its `trap : INT` handler). The kernel's post-run path detects
the cancel via per-cell-id tracking and publishes `COMMAND_CANCELLED`
(payload: `cell_id`, `exit_code`) instead of `COMMAND_COMPLETED` /
`COMMAND_FAILED`, with partial output preserved on the cell. Ctrl+C
with nothing running surfaces a `HELP_REQUESTED` hint rather than
silently swallowing the keystroke. Needs the F62 async-execution
worker so the keystroke can reach the router while a command is in
flight; the CLI turns the worker on by default.

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

**Status: Shipped.** `:reload-bank` (INPUT-mode meta-command)
re-reads the on-disk bank via `SoundBank.load()` and swaps it into
the running `SoundEngine`. The meta-command resolves the path from
the `bank_path` the CLI passed to `Application.build`; without one
the command surfaces a `HELP_REQUESTED` hint instead of silently
no-oping. Parse failures (corrupt JSON, broken references) and
missing files surface the same way so the user hears exactly why
the reload did not happen. `BANK_RELOADED` fires on success with
`path` and `binding_count` so the default bank narrates "bank
reloaded from disk". Refuses while the settings editor is open so
in-flight edits are never silently discarded. Ctrl+R in SETTINGS
mode stays on F21c (reset-to-defaults); the meta-command is
reachable from the daily INPUT workflow where the user was already
typing. Covered by `test_reload_bank_meta_command_swaps_live_bank`
and peers in `tests/test_app.py`.

---

## F4 — Command history

**Status: Shipped.** (Up / Down recall). `Session` now carries a
`command_history: list[str]` populated at `record_command` time —
empty / whitespace-only commands are dropped and consecutive
duplicates collapse so the user doesn't have to walk past the same
`pytest` invocation ten times. `NotebookCursor.history_previous` /
`history_next` walk that list while in INPUT mode; Up replaces the
buffer with the previous command (caret at end), Down steps forward,
and Down past the most recent entry restores whatever the user had
typed before they started browsing. Typing or any other buffer
mutation clears the browse state so the next Up restarts from the
most-recent entry. Bound to Up / Down in INPUT mode (the keys still
mean "walk cells" in NOTEBOOK mode and "step lines" in OUTPUT mode
because the binding map is per focus mode). The router's
`history_previous` / `history_next` actions publish a
`recalled: bool` payload so observers can voice an "empty history"
hint. `command_history` round-trips through `Session.to_dict` /
`from_dict` so resuming a saved session preserves the walk.

**Deferred.** Ctrl+R reverse-incremental search is still open; it
needs a composer overlay analogous to the SETTINGS `/` search and is
worth its own follow-up PR.

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

**Status: Shipped.** `asat/tui_bridge.py::_classify_osc` now splits
OSC 133 by subcommand into four distinct categories — `prompt_start`
(`133;A`), `prompt_end` (`133;B`), `command_start` (`133;C`),
`command_end` (`133;D`) — plus a generic `prompt` for any unknown
subcommand. The default bank ships a `prompt_ready` 990 Hz / 30 ms
tone on the `system` voice, gated on `category == "prompt_start"`,
so users running zsh + powerlevel10k, starship, kitty, or vscode's
shell-integration get an audible "shell is ready" cue out of the
box. The other subcommands stay silent so the default doesn't fire
four blips per command; users can clone the recipe in the editor to
sonify command-end (with exit code) once F7 follow-ups land.

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

**Status: Shipped.** See the repo-root [README.md](../README.md).

---

## F10 — Non-blocking execution + cancel keystroke

**Status: Shipped.** `ExecutionKernel` runs each cell on a worker
thread under `_cancel_lock`/`_cancelled_cells` state, exposes
`cancel(cell_id)` that terminates the subprocess and publishes
`COMMAND_CANCELLED`, and the router binds Ctrl+C in INPUT mode to
that path (the F1 entry tracks the keystroke shipping).

**Gap (at time of shipping).** `Application.execute(cell_id)` ran the
kernel synchronously, so while a command was running the entry-point
loop could not read keys. That made F1 (Ctrl+C cancel) impossible to
implement without moving execution off the main thread.

**Where it surfaces.** Every long-running command — `pytest`, `npm
install`, `git clone` — blocks the whole terminal until it finishes.

**Sketch.** Run the kernel on a worker thread, keep the main loop
draining keys. Use the existing `EventBus` to communicate: the
worker publishes output/completion events the main loop subscribes
to. Add `ExecutionKernel.cancel(cell_id)` that terminates the
subprocess and publishes `COMMAND_CANCELLED`; bind Ctrl+C to it.

---

## F11 — Auto-advance after submit

**Status: Shipped.** `NotebookCursor.submit()` now appends a fresh
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

**Status: Shipped (superseded by F60).** F60's persistent
`ShellBackend` handles pipes, redirects, globbing, `$VAR`
expansion, and shell builtins (`cd`, `export`) without a separate
ExecutionMode toggle. Session-level CWD persists across cells
because the long-lived shell keeps its own cwd between commands.
The CLI exposes the shared shell via `--shell` / `ShellBackend`
construction; a dedicated `:shell on/off` toggle was not shipped
because the backend choice is now launch-time and session-scoped.

**Gap (at time of filing).** `ExecutionMode.ARGV` was the default, so pipes, redirects,
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

**Status: Shipped.** `FocusState` now carries `cursor_position`, and
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

**Status: Shipped.** `Application.build()` now wires a `MemoryClipboard`,
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

**Status: Shipped.**

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

**Status: Shipped.** (first pass).

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

**Status: Shipped.**

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

**Status: Shipped.**

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

**Status: Shipped.**

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

**Status: Shipped.**

**Gap (at time of shipping).** There was no way to record a
session's events to disk for later review. A blind user who
wanted to post-mortem what happened during a long-running command
had only what the audio engine and text trace produced in real
time; sharing a reproduction with a maintainer required
remembering exactly what they heard.

**Sketch (shipped).** New `asat/jsonl_logger.py` defines
`JsonlEventLogger`, a wildcard bus subscriber that writes one
JSON line per event (`event_type`, `payload`, `source`,
`timestamp`). The stream opens in `"w"` mode so each session
starts from a clean file; parent directories are created on the
fly; unserialisable payload values fall back to `repr`. A
`--log PATH` CLI flag (`asat/__main__.py`) plumbs a `log_factory`
through `Application.build`, which attaches the logger BEFORE
any startup publish so `SESSION_CREATED` and the initial
`FOCUS_CHANGED` land in the file. `Application.close()` flushes
and unsubscribes the logger; further publishes are silently
dropped (the logger is idempotent on close). Tests: seven cases
in `tests/test_jsonl_logger.py` (write-per-event, wildcard
capture, truncation, mkdir, unsubscribe-on-close, idempotent
close, repr fallback) plus two Application-level integration
tests.

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

**Status: Shipped.** `p` / `Space` in OUTPUT mode toggle an
`OutputPlaybackDriver` that auto-advances `OutputCursor` one line
every 1.5 s on a daemon ticker. Each tick publishes the usual
`OUTPUT_LINE_FOCUSED` so the narration pipeline reads lines end-to-
end without special casing. `OUTPUT_PLAYBACK_STARTED` / `_STOPPED`
bookend the run; reasons are `"end"` (buffer exhausted),
`"cancelled"` (any other key), or `"focus_changed"` (leaving
OUTPUT mode). The bank-driven `voices.narrator.playback_rate` knob
from the original sketch is left for a follow-up — the fixed
cadence handles the "catching up on a long build log" use case
today.

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

**Status.** Partially shipped as F61 (headings), an F27
text-cell slice, F27 parent-scope navigation, the
`asat/outline.py` scope helper with
`NotebookCursor.select_heading_scope()`, and F27 fold / collapse
(`z`, `OUTLINE_FOLDED` / `OUTLINE_UNFOLDED`). Heading cells,
flat outline navigation (`]` / `[`, `1`-`6`), `:toc`, `:heading
<level> <title>`, and the default-bank heading voice are in
(see F61). Text cells now exist as `CellKind.TEXT` with a
`:text <prose>` INPUT meta-command and a dedicated
`focus_changed_text` narration binding. `{` / `}` in NOTEBOOK
walk to the previous / next heading whose level is strictly
shallower than the current scope (enclosing heading).
`asat/outline.scope_range(cells, heading_index)` returns the
`[start, end)` span of a heading's section (children included),
and `NotebookCursor.select_heading_scope()` returns the focused
cell's enclosing section as a list of Cells. `z` on a heading
toggles a `collapsed` flag — Up/Down skip over the hidden
scope, and `OUTLINE_FOLDED` / `OUTLINE_UNFOLDED` carry the
heading's metadata and hidden-cell count. What is still pending
from the original F27 sketch: the NOTEBOOK `i` keybinding for
in-place text insertion.

**Gap.** Every cell today is an input / output pair produced by
`ExecutionKernel` or an announce-only heading (F61). There is no
*text* cell for prose, so users who want to document a long
exploration still have to pile narrative into a `#`-prefixed
shell comment or a heading title.

**Where it surfaces.** A session that walks through "set up fixtures
→ train a model → evaluate" can now be announced by section (F61),
but the prose inside each section — "we train for ten epochs
because earlier runs overfit at twenty" — has no home. F51
persists the heading fields added by F61; the same loader
already accepts the reserved `"text"` kind, so adding the cell
type is a narrow change.

**Sketch — remaining work after F61.**

1. **Text cell kind.** *(Text-cell slice shipped.)* `CellKind.TEXT`
   and a `text: str` field on `Cell` are in; `is_executable`
   returns False so `Application.execute` short-circuits (same
   guard F61 added for headings). INPUT's `:text <prose>`
   meta-command appends a text cell and abandons INPUT (parallels
   `:heading`), and FOCUS_CHANGED narrates "text, {text}". Still
   pending: the NOTEBOOK `i` binding for in-place insertion
   (needs an in-place editor for the text body; typing-in-line
   is not yet wired).
2. **Parent-scope navigation.** *(Shipped.)* `{` / `}` in NOTEBOOK
   jump to the previous / next heading whose `heading_level` is
   strictly shallower than the current scope.
   `NotebookCursor.move_to_{next,previous}_parent_heading` define
   scope as "the focused heading's level, or the nearest preceding
   heading's level for a non-heading cell". Heading narration
   piggy-backs on FOCUS_CHANGED; no-match falls through silently
   like the flat `]` / `[` nav.
3. **Scope selection + fold / collapse.** *(Shipped.)*
   `asat/outline.py` provides the pure
   `scope_range(cells, heading_index) -> (start, end)` function,
   `enclosing_heading_index(cells, index)`, and `visible_indices(cells)`
   (the index list not hidden by any collapsed heading).
   `NotebookCursor.select_heading_scope()` returns the focused
   cell's enclosing section as a list of Cells, ready for F26's
   clipboard or any future outline-aware action. `Cell.collapsed`
   is a HEADING-only boolean flag; `NotebookCursor.toggle_fold_focused_heading`
   flips it and publishes `OUTLINE_FOLDED` /
   `OUTLINE_UNFOLDED` with the heading's id, level, title, and
   hidden-cell count (`scope_range.end - start - 1`). The `z`
   keystroke from NOTEBOOK invokes the toggle, and `move_up` /
   `move_down` consult `visible_indices` so keyboard navigation
   skips over the hidden scope. Default-bank bindings narrate
   "section X collapsed, N cells" / "section X expanded".

**See also.** F51 (notebook file format) persists the heading
fields F61 added; populating `"text"` piggy-backs on the same
path. F61 already ships with `Cell.kind` defaulting to
`COMMAND` on load, so adding `TEXT` is additive.

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

**See also.** This entry is the in-memory foundation for the
F50 – F55 workspace cluster. F53 (focus memory) and F55
(persistent window state) both assume a `TabBar` with
`list[NotebookTab]` and `focus_index`; implement that
structure here even if the per-tab kernel half of F29 slips.
F54's `:new-notebook` / `:close-tab` meta-commands drive this
tab bar.

---

## F30 — Audio history / "repeat last narration"

**Status: Shipped (minimal tier — F30a).**

**Gap (at time of shipping).** When a narration passed by faster
than the user could absorb it — or a new event spoke over the
one they wanted to catch — there was no way to re-hear the last
phrase. Assistive tech on desktops universally provides a
"say-it-again" key; ASAT did not.

**Sketch (shipped).** `SoundEngine` (`asat/sound_engine.py`)
gained a bounded `collections.deque(maxlen=20)` of
`NarrationHistoryEntry(event_type, binding_id, text, voice_id)`
that records every rendered speech phrase. Sound-only bindings
(no voice) are intentionally skipped so the replay only offers
actual phrases. `replay_last_narration()` re-synthesises the
last entry through the same voice, plays it via the sink, and
publishes `NARRATION_REPLAYED`. The replay path bypasses
bindings so it does not recurse back into the history. When the
voice has been removed from the current bank the method returns
`None` gracefully. `InputRouter` (`asat/input_router.py`) binds
`Ctrl+R` to `repeat_last_narration` in NOTEBOOK and INPUT (the
SETTINGS mode keeps its own `Ctrl+R` for `settings_reset_begin`)
and surfaces `:repeat` as an ambient meta-command that keeps
INPUT focus. `Application._on_action_invoked`
(`asat/app.py`) catches either form and drives
`sound_engine.replay_last_narration()`. Tests: seven new cases
in `NarrationHistoryTests` (`tests/test_sound_engine.py`), three
router cases (`tests/test_input_router.py`), three Application
cases (`tests/test_app.py`). The Ctrl+Shift+R history overlay
is left as F30b for a future sweep.

---

## F31 — Narration verbosity presets

**Status: Shipped.** `EventBinding` carries `verbosity`
(`minimal` / `normal` / `verbose`, default `normal`) and `SoundBank`
carries a matching `verbosity_level` ceiling. `bindings_for` now
filters by verbosity by default so the engine only sees the bindings
the current preset allows; the editor passes `respect_verbosity=False`
to keep surfacing every record. `:verbosity <level>` calls
`SoundEngine.set_verbosity_level`, which swaps the bank and publishes
`VERBOSITY_CHANGED` — the default bank binds that event at the
`minimal` tier so the user always hears the new preset, even when
they just dropped to `minimal`. Round-trips through JSON and the
schema documents both fields. `Ctrl+M` cycling inside SETTINGS and a
settings-editor row for the ceiling remain as follow-ups.

**Gap (at time of shipping).** Some users want bare-minimum narration
(errors + exit codes only), others want chatty feedback on every
keystroke. Today, the only way to quiet a class of events is to
manually disable bindings one-by-one in the settings editor.

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

**Status: Shipped.** `SoundBank` carries `ducking_enabled` (default
`True`) and `duck_level` (default `0.4`); the engine multiplies any
concurrent non-speech cue by `duck_level` whenever a speech buffer
is in the same `_render` mix cycle. Both fields round-trip through
JSON and the schema documents the bounds. Live-edit today is via
the bank file plus `:reload`; an editor surfacing pass can land
later if needed.

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

**Status: Shipped.**

**Gap (at time of shipping).** A long-running command that
completed in the background fired the normal completion cue, but
if the user had moved to OUTPUT mode on a different cell, the cue
was easy to miss. Shells historically rang the terminal bell; ASAT
had no equivalent "I'm done, come back" signal.

**Sketch (shipped).** New `asat/completion_alert.py` defines
`CompletionFocusWatcher`, which shadows `FOCUS_CHANGED` (reading
`new_cell_id`) and listens for `COMMAND_COMPLETED` /
`COMMAND_FAILED`. When a completion's originating `cell_id`
differs from the user's current focus, the watcher publishes an
additional `COMMAND_COMPLETED_AWAY` event carrying both cell ids,
the `original_event_type` (`command.completed` or
`command.failed`), `exit_code`, and `timed_out`. Two-tier
semantics: the normal completion cue still fires; the away-cue is
a bonus nudge. The watcher explicitly does NOT fire when the
shadow focus is still `None` (no `FOCUS_CHANGED` yet), so the very
first command never produces a spurious away nudge. The default
bank (`asat/default_bank.py`) adds the `alert_away` recipe
(chord 440/659.25/880, azimuth 55°, elevation 10°) and a
`command_completed_away` binding on the `alert` voice with
template `"completed in background"` and priority 225. Silenceable
via the same per-binding toggle every cue has. Tests: seven cases
in `tests/test_completion_alert.py` plus one Application-level
integration test that drives a real cell through the kernel and
asserts the away event fires when focus has moved.

---

## F35 — Cell bookmarks

**Status: Shipped.** (`:bookmark` / `:jump` / `:bookmarks` /
`:unbookmark`). The session now owns a `bookmarks: dict[str, str]`
field (round-tripped in `to_dict`/`from_dict`) plus
`add_bookmark` / `remove_bookmark` / `get_bookmark` /
`list_bookmarks` helpers. `Session.remove_cell` automatically prunes
any bookmark whose target was removed so `:jump` can never resolve
to a stale id. Three new event types — `BOOKMARK_CREATED`,
`BOOKMARK_JUMPED`, `BOOKMARK_REMOVED` — fire from the router so
narration / audio cues can hook in without monkey-patching session
state. The router's INPUT-mode meta-command set gained the four new
verbs (the first three ambient — they leave the user typing; `:jump`
is non-ambient because focus moves). Names are single tokens with
surrounding whitespace stripped; reusing a name rebinds it. Future
work: tab-completion when F23 lands and a per-bookmark spatial cue
in the default bank.

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

**Status: Shipped.**

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

**Status: Shipped.**

**Gap (at time of shipping).** A command emitting thousands of lines
produced a continuous narration stream. After thirty seconds the user
had lost sense of progress and the per-line cues became noise. There
was no silence detection ("it's been quiet for 10s — still running?")
and no periodic progress beat during streaming.

**Sketch (shipped).** New `asat/streaming_monitor.py` holds a
`StreamingMonitor` that subscribes to `COMMAND_STARTED`,
`OUTPUT_CHUNK`, `ERROR_CHUNK`, `COMMAND_COMPLETED`/`_FAILED`/
`_CANCELLED` and tracks per-cell streaming state. `check(now=None)`
publishes `OUTPUT_STREAM_PAUSED` once per quiet window
(`silence_threshold_sec`, default 5.0) and `OUTPUT_STREAM_BEAT`
every `progress_beat_interval_sec` (default 30.0) the stream is
alive. `Application.build` constructs the monitor, wires it onto
the bus alongside `CompletionFocusWatcher`, and launches a daemon
ticker via `start_background_ticker()` that polls `check()` every
second; `Application.close` stops the ticker. The default bank
binds both events to two new subtle cues (`stream_paused`,
`stream_beat`) at the default "normal" verbosity tier, so they play
out of the box — `minimal` banks skip them, and users who want
total silence during long builds disable the bindings in settings.
Tests inject a virtual `clock` and drive
`check(now)` directly, so no real sleeps are ever needed.

---

## F38 — Self-voicing help topics

**Status: Shipped.**

**Gap (at time of shipping).** `:help` printed the cheat sheet. A
brand-new blind user needed a spoken tour of each mode, the key
keystrokes, and discovery paths like `Ctrl+,` for settings — the
only alternative was reading the USER_MANUAL in a screen reader.

**Sketch (shipped).** New `asat/help_topics.py` holds a
`HELP_TOPICS: dict[str, tuple[str, ...]]` with six topics:
navigation, cells, settings, audio, search, meta. Each topic is
a short spoken tour (heading + a handful of body lines).
`asat/input_router.py` `_publish_help(argument)` dispatches:
bare `:help` → `HELP_LINES` (unchanged); `:help topics` → an
enumeration of every registered topic with a `help_topic:
"topics"` payload key; `:help <topic>` → the topic's lines with
`help_topic: <name>`; unknown topic → a `HELP_REQUESTED` hint
with a `difflib.get_close_matches` suggestion and
`help_topic_unknown` key. Topic lookup is case-insensitive.
Tests: four new router cases (`test_colon_help_topic_*`) plus
five in a new `tests/test_help_topics.py`. `HELP_LINES` gained a
row pointing users at `:help topics` so the self-voicing path is
discoverable from the cheat sheet itself.

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

**Status: Shipped.** `asat/self_check.py` implements the four-step
routine (`bank_validates`, `voices_speak`, `event_cues`,
`live_playback`); `python -m asat --check` prints the diagnostic
header, runs the self-test, publishes one `SELF_CHECK_STEP` event
per step, and returns exit code 0 on full pass / 1 on any failure.
Reference payloads live in `asat/sample_payloads.py` so the same
canonical data drives both `tests/test_default_bank.py` and the
self-check. See "Diagnosing audio issues" in `docs/USER_MANUAL.md`.

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

**Status: Shipped.**

**Gap (at time of shipping).** F20 narrated a welcome and explained
the key meta-commands but stopped there. The user was then dropped
into an empty notebook with no prompt to run anything, so the
first-run experience ended on a passive "press colon h e l p" —
the newcomer still had to invent their own first command to hear
what `COMMAND_SUBMITTED` / `COMMAND_STARTED` / `COMMAND_COMPLETED`
/ exit-code narration sounded like.

**Sketch (shipped).** `asat/onboarding.py` gained
`FIRST_RUN_TOUR_COMMAND = "echo hello, ASAT"` + a short
`FIRST_RUN_TOUR_LINES` prompt and a new
`OnboardingCoordinator.publish_tour_step(...)` helper.
`Application.build()` reads `onboarding.is_first_run()` *before*
`onboarding.run()` flips the sentinel; when it's a first run and
the build seeded a fresh session, `cursor.new_cell(...)` is called
with the tour command so the newcomer's first Enter press exercises
the full submit → start → complete → exit-code arc. The tour step
fires after the welcome event so the audio order is "welcome, then
prompt". If the user clears or rewrites the pre-filled line before
pressing Enter, the tour is considered complete — the coordinator
never re-inserts. A new `FIRST_RUN_TOUR_STEP` event in
`asat/events.py` carries `command` + `lines`; the default bank
binds it to the narrator voice. `--quiet` / `--check` skip
onboarding entirely, which transparently skips the tour too.

---

## F44 — `:welcome` meta-command to replay onboarding

**Status: Shipped.**

**Gap (at time of shipping).** Once the first-run sentinel at
`~/.asat/first-run-done` was written, there was no supported
path to re-hear the onboarding narration. Users who missed a
word, returned after a hiatus, or wanted to demo ASAT had to
manually delete the sentinel and relaunch.

**Sketch (shipped).** `OnboardingCoordinator.run()` gained a
keyword-only `force: bool = False` parameter. When
`force=True`, the coordinator publishes `FIRST_RUN_DETECTED`
with `replay=True` in the payload and **does not** rewrite the
sentinel, preserving F20's once-per-machine contract. The F41
silent-sink hint is also skipped on replays. Added `"welcome"`
to both `META_COMMANDS` and `AMBIENT_META_COMMANDS` in
`asat/input_router.py` so `:welcome` propagates as a
`meta_command: "welcome"` payload on ACTION_INVOKED without
taking focus out of INPUT mode. `Application._on_action_invoked`
catches the meta-command and calls `onboarding.run(force=True)`;
when `onboarding is None` (--quiet or --check) the meta-command
is a harmless no-op. Tests: three new coordinator cases, two
Application cases, one router case.

F43 (guided first-command tour) follow-up: `:welcome tour` as a
richer variant remains on the F43 entry once that lands.

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

**Status: Shipped.** Every bullet below landed in its own PR
(#76 None-guard decorator, #77 search sentinel, #78 _action_handler
split, #79 FIELD_PARSERS, #80 _META_HANDLERS, #81 state-machine
doc links). The `ComposerMode(str, Enum)` bullet had already
shipped against `asat/output_cursor.py` before this entry was
formalised.

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
docstring in the touched module; no user-manual changes.

---

## Cluster: multi-notebook workspaces (F50 – F55)

F50 – F55 form one architectural proposal, not six independent
features. Land them in order: each entry depends on the previous
one, and implementing a later entry without its predecessors
leaves the codebase in a worse state than it is today.

Single-sentence vision: **one ASAT process manages one workspace
directory, which holds multiple persistent notebooks opened as
tabs in a single window, with focus and settings remembered per
tab.**

The cluster sits adjacent to two earlier entries — keep them
cross-referenced as you work:

- **F27** (heading and text cells) defines *what* a notebook
  contains. F51's on-disk schema must cover every cell kind F27
  introduces; implementing F51 first and F27 later is fine as
  long as the schema is forward-compatible.
- **F29** (tabs + per-tab backend kernel) defines *how* tabs
  work at the `Application` layer. F53 (focus memory) and F55
  (window state persistence) both assume F29's `TabBar`
  scaffolding is in place. F29 may land before or alongside
  F50, but no tab-level persistence work should start before F29
  ships.

The recommended landing order is:

1. **F50** — directory layout, marker file, bootstrap.
2. **F29** — in-memory tabs + per-tab kernels (if not shipped).
3. **F51** — on-disk notebook format + save/load.
4. **F54** — CLI + `:workspace` meta-commands.
5. **F53** — per-tab focus memory.
6. **F55** — persisted window state (which tabs are open).
7. **F52** — settings cascade (can be started any time after F50).

Each of F50 – F55 below contains a verbose, implementation-ready
sketch: exact file layouts, JSON schemas, key bindings, event
types, and test strategies. They are written to be picked up
months from now by someone (possibly Claude) who no longer has
the cluster in short-term context.

---

## F50 — Workspace directory model

**Status: Shipped (minimal slice).** A minimal slice has shipped:
[`asat/workspace.py`](../asat/workspace.py) defines the
`Workspace` handle, the `<root>/.asat/{config.json,log/}` +
`<root>/notebooks/*.asatnb` layout, `init` / `load` /
`is_workspace` / `find_enclosing` / `notebook_path` /
`new_notebook` / `resolve_cwd` / `set_last_opened` /
`default_notebook`, and per-notebook `Session.cwd`. The
`Application.build` ctor accepts a `workspace` and chdir's into
it before publishing `WORKSPACE_OPENED` + `NOTEBOOK_OPENED`.
Deferred to a follow-up: advisory file locking
(`.asat-workspace.lock`, fcntl/msvcrt), per-notebook sidecars
(`<slug>.asatnb.state`), and the workspace-level
`settings/bank.json` cascade — the default bank still loads
from `~/.asat/bank.json` only.

**Gap.** ASAT has no concept of a "workspace". State that should
be persistent (the cells a user is building up, which tabs are
open, workspace-scoped settings) is either in-memory only or
stored at a single global path (`~/.asat/bank.json`). There is
no way to keep two unrelated projects' notebooks and sounds
separate, no way to share a folder full of ASAT work with a
collaborator, and no way to "open" a previous session.

**Where it surfaces.** A user with two long-running efforts
(training runs + sysadmin scratch) has to manually export and
re-import the bank if they want different audio cues per
project. Closing ASAT loses every command cell typed. A teammate
cannot hand over a directory and say "open this in ASAT" —
there is no such concept.

**Sketch.** Define a workspace as an absolute directory path
whose layout is:

```
<workspace>/
  .asat-workspace         # marker file, JSON: {"schema_version": 1, "created_at": ISO8601}
  notebooks/              # one file per notebook (see F51)
    <slug>.asatnb
    <slug>.asatnb.state   # per-notebook sidecar (cursor, focus mode, last opened)
  settings/
    bank.json             # workspace-level sound-bank overrides (see F52)
    window.json           # window state: open tabs, focus_index, timestamps (see F55)
  logs/                   # optional, created on demand by F22 diagnostic log
    events.jsonl
```

The `.asat-workspace` marker file is the single source of truth
for "is this directory an ASAT workspace?". Its presence is both
necessary and sufficient. The file itself carries a minimal
JSON envelope: `{"schema_version": 1, "created_at": ISO8601}`;
the rest of the workspace layout is implicit.

Introduce `asat/workspace.py` with:

- `@dataclass(frozen=True) class Workspace(root: Path)` — the
  resolved, validated handle. Construction does *not* mutate the
  filesystem; it only checks that `root` exists and (if it is
  claimed to already be a workspace) that `.asat-workspace`
  parses.
- `Workspace.bootstrap(root: Path) -> Workspace` — class method
  that creates `root` if missing, writes the marker file,
  creates `notebooks/` and `settings/` subdirectories, and
  returns the handle. Idempotent: re-bootstrapping an existing
  workspace is a no-op that validates the marker.
- `Workspace.is_workspace(root: Path) -> bool` — cheap check
  used by F54 discovery logic.
- `Workspace.notebooks_dir`, `Workspace.settings_dir`,
  `Workspace.logs_dir` — `Path` properties. No I/O; just join.
- `Workspace.notebook_paths() -> list[Path]` — sorted list of
  `*.asatnb` files under `notebooks/`; used at window startup
  to restore open tabs (F55).

Events published on the bus:

- `WORKSPACE_OPENED` — payload `{path, created_at}`; fired when
  an `Application` binds to a workspace. Subscribers (the sound
  bank, the logger, the tab bar) rehydrate state.
- `WORKSPACE_CLOSED` — payload `{path}`; fired during shutdown
  after all tabs flush.

**Locking & concurrency.** Two ASAT processes must not write to
the same workspace simultaneously. Implement advisory locking
with a `.asat-workspace.lock` file holding `{pid, hostname,
started_at}`. On `Workspace.bootstrap`/open, check for a stale
lock (process no longer exists) and offer to steal it; on a
live lock, refuse to open and narrate the conflict. File locking
uses `fcntl.flock` on POSIX and `msvcrt.locking` on Windows,
wrapped behind a small `asat/_filelock.py` helper. The helper
must be kept tiny and OS-neutral; no third-party dependency.

**Migration / backwards compat.** For users launching ASAT the
old way (no path argument), F54 will pick a default workspace
path; no existing on-disk data is rewritten. `~/.asat/bank.json`
remains the user-level bank (see F52's cascade).

**Tests.** `tests/test_workspace.py`:

- `test_bootstrap_creates_layout` — fresh `tmp_path`, expect
  `.asat-workspace`, `notebooks/`, `settings/` after bootstrap.
- `test_bootstrap_idempotent` — second bootstrap does not
  overwrite the marker's `created_at`.
- `test_is_workspace_requires_marker` — directory without
  marker returns False even if it has a `notebooks/` folder.
- `test_stale_lock_is_steal_able` — seed a lock file with a PID
  that definitely is not running; expect steal to succeed and
  narrate.
- `test_live_lock_refuses` — hold a real `flock`; expect open
  to fail with a `WorkspaceBusy` error.

**Docs touch points.**
[`USER_MANUAL.md`](USER_MANUAL.md) gains a "Workspaces" section
that explains the layout, the marker, and the lock file. A new
[`docs/WORKSPACES.md`](WORKSPACES.md) owns the full reference
(directory layout table, JSON schemas cross-linked to F51/F52/F55,
concurrent-use semantics).

---

## F51 — Persistent notebook file format (`.asatnb`)

**Gap.** Cells live exclusively in memory on the active
`Session`. Closing ASAT discards every command, output, and
timing datum. There is no file format, no save operation, no
load operation, and nothing on disk that could represent a
notebook to a future ASAT run or to a collaborator.

**Where it surfaces.** A user who spent two hours walking
through a deploy runbook cannot re-open it tomorrow to continue,
share it with a teammate, attach it to a ticket, or diff today's
run against last week's. Auditors cannot reconstruct "what
commands did this user issue, when, and with what exit codes?".

**Sketch.** Define a JSON-based file format, one notebook per
file, stored under `<workspace>/notebooks/<slug>.asatnb`. JSON
is the right choice: human-readable, git-diffable, stdlib only,
zero ambiguity for screen readers that might inspect the file
directly. Binary formats and SQLite are rejected on those
grounds.

**Top-level schema (schema_version 1).**

```json
{
  "schema_version": 1,
  "notebook_id": "nb-01HZS4VXTWkCieZ8LS5KyoBS",
  "title": "deploy runbook",
  "created_at": "2026-04-19T12:34:56Z",
  "modified_at": "2026-04-19T15:02:11Z",
  "cells": [ ... ],
  "settings": { ... }
}
```

Field rules:

- `schema_version` (int, required): start at `1`; bump on any
  breaking change. Loader refuses unknown versions with a
  clear error pointing at `docs/WORKSPACES.md#migration`.
- `notebook_id` (str, required): stable across renames; used by
  F55 to identify tabs in `window.json` even if the file is
  renamed on disk.
- `title` (str, required): user-visible label shown in the tab
  announcement (F29). Defaults to the file's slug at creation.
- `created_at` / `modified_at` (ISO 8601, UTC, required): the
  loader must refuse a file whose `modified_at < created_at`.
- `settings` (object, optional): notebook-scoped overrides
  consumed by F52's cascade. Missing = inherit everything.

**Cell schema.** Every cell has `{id, kind, created_at}` plus
kind-specific fields. The loader discriminates on `kind`:

```json
// kind: "input"  (F4/F19/F36 persistence lives here)
{
  "id": "cell-01HZS4W...",
  "kind": "input",
  "created_at": "2026-04-19T12:35:01Z",
  "command": "pytest -q",
  "output_lines": ["....", "8 passed in 1.2s"],
  "exit_code": 0,
  "started_at": "2026-04-19T12:35:01Z",
  "finished_at": "2026-04-19T12:35:02.2Z",
  "timed_out": false
}

// kind: "heading"  (introduced by F27)
{
  "id": "cell-01HZS4X...",
  "kind": "heading",
  "created_at": "2026-04-19T12:35:30Z",
  "text": "Data preparation",
  "level": 2
}

// kind: "text"  (introduced by F27)
{
  "id": "cell-01HZS4Y...",
  "kind": "text",
  "created_at": "2026-04-19T12:36:00Z",
  "text": "Verify the fixture directory is clean before the run."
}
```

Forward compatibility: the loader ignores unknown fields within
a known cell kind (preserves them on re-save as opaque blobs),
and refuses unknown `kind` values with a clear error. This lets
a newer ASAT open an older file and an older ASAT fail loudly
on a newer file instead of silently dropping cells.

**What is *not* persisted.** Runtime state — subprocess handles,
PTY buffers, live ANSI parser state, unflushed stderr — is
deliberately dropped on save. On reload, every cell is marked
"historical": re-executing requires the user to explicitly press
Enter on the cell, which submits `command` afresh on the
current kernel. This keeps the format purely declarative and
avoids any attempt to "resume" a running process.

**Module surface.** Introduce `asat/notebook_io.py`:

- `save_notebook(notebook: Notebook, path: Path) -> None` —
  atomic write via `path.with_suffix(".asatnb.tmp")` + rename.
- `load_notebook(path: Path) -> Notebook` — returns a fresh
  `Notebook` with every `Cell` reconstructed; raises
  `NotebookFormatError` on schema mismatch with a diagnostic
  message that names the offending field and offset.
- `Notebook` mirrors today's `Session` but is purely in-memory
  data (no kernel reference); the existing `Session` becomes
  `Notebook + ExecutionKernel + NotebookCursor` composed at the
  tab layer (F29). This refactor should be quietly prepared in
  advance of F51 so the file-IO layer talks to a plain data
  object.

**Save triggers.** Three policies, selectable in workspace
settings (default = "idle"):

- `"manual"` — only `:save` / `Ctrl+S` persists.
- `"idle"` — save 2 seconds after the last keystroke, capped at
  one save per 10 seconds. Implemented with a small
  `IdleSaver` that listens to `CELL_CREATED`,
  `CELL_MODIFIED`, `COMMAND_COMPLETED`, etc.
- `"every-change"` — save on every cell-mutating event. Useful
  for paranoid audit contexts; hits disk often.

Publish `NOTEBOOK_SAVED` / `NOTEBOOK_LOADED` events on the bus
so the sound bank can play a confirmation cue and F22's log
captures the lifecycle.

**Tests.** `tests/test_notebook_io.py`:

- `test_roundtrip_preserves_every_cell_kind` — build a
  `Notebook` with one cell of each kind, save, load, assert
  equality.
- `test_unknown_schema_version_refuses_load` — file with
  `schema_version: 999` raises `NotebookFormatError` naming the
  version.
- `test_unknown_cell_kind_refuses_load` — same, for cells.
- `test_unknown_field_within_known_cell_survives_roundtrip` —
  injected `{"future_field": 42}` comes back on re-save.
- `test_atomic_write_leaves_no_partial_file_on_crash` —
  monkeypatch the temp-rename step to raise mid-write; original
  file must remain untouched.

**Docs touch points.** `docs/WORKSPACES.md` grows a "Notebook
file format" chapter with the full JSON schema, migration notes,
and a worked example. `USER_MANUAL.md` gains `Ctrl+S` /
`:save` / `:save as <name>` rows.

---

## F52 — Workspace & notebook settings cascade

**Gap.** `SoundBank` is loaded from exactly one path (today
`~/.asat/bank.json`, after F45 relocatable via `ASAT_HOME`).
There is no mechanism to overlay a project-specific bank on top
of the user bank, nor a notebook-specific bank on top of that.
Two projects cannot have different audio cues without the user
manually pointing `--bank` at the right file each launch.

**Where it surfaces.** A user who prefers loud cues for long
training runs but soft cues for ordinary sysadmin work has no
way to express that per-workspace. A teammate who hands over
a workspace expects their custom cues to travel with the
directory; today they don't.

**Sketch.** Introduce a four-layer settings cascade resolved
at workspace-open time:

```
Layer 0 — DEFAULTS     (hard-coded in asat/default_bank.py)
Layer 1 — USER         (~/.asat/bank.json; or $ASAT_HOME/bank.json)
Layer 2 — WORKSPACE    (<workspace>/settings/bank.json)
Layer 3 — NOTEBOOK     ((.asatnb).settings, see F51)
```

Each layer is optional; missing layers are skipped. Higher
layers *override* matching keys in lower layers, identified by
`(section, id)`. Sections are the three existing bank sections:
`voices`, `sounds`, `bindings`.

- **Voices**: override by `voice_id`. A workspace that redefines
  `voice_id: "cue"` with a different speed fully replaces the
  user voice's fields; fields not mentioned fall back to the
  lower layer. This is a deep merge per record.
- **Sounds**: same — override `recipe_id`, shallow-merge fields.
- **Bindings**: override by `binding_id`; bindings are replaced
  as a whole record (not merged) because their semantics depend
  on the combination of predicate + sound, which a partial
  override would scramble.

Add keys at each layer can *add* new records that do not exist
below; this is how a workspace introduces a workspace-specific
cue. Removing a record requires an explicit tombstone entry:
`{"id": "cue.old", "deleted": true}`, which suppresses a lower
layer's record in the effective bank. Tombstones are rare and
only necessary when a user wants to silence a built-in.

Introduce `asat/settings_cascade.py`:

- `resolve_bank(layers: list[SoundBank]) -> SoundBank` — pure
  function; takes the ordered list `[defaults, user, workspace,
  notebook]` (each optional), returns the merged bank. No I/O.
- `load_layered_bank(workspace: Workspace | None, notebook:
  Notebook | None) -> SoundBank` — orchestrator; handles
  "workspace not open yet" and "no notebook focused yet".

**Events.**
- `BANK_RESOLVED` fires whenever the effective bank changes
  (workspace open, notebook focus change, save of any layer).
  Payload: `{sources: list[str], voice_count, sound_count,
  binding_count}`.

**Settings editor impact.** `SettingsEditor` today edits one
bank. After F52 it must know which layer it is editing; the
user picks the layer on entry (default: workspace if a
workspace is open, else user). A mode indicator in the editor
status line reads `editing: workspace bank` / `editing: user
bank` so the user is never confused about what they are
modifying. Saving writes the layer's file; other layers are
untouched.

**Tests.** `tests/test_settings_cascade.py`:

- `test_missing_layers_skip_silently` — pass `[defaults, None,
  None, None]`; result equals defaults.
- `test_workspace_override_wins_over_user` — user sets
  `voice.cue.rate=150`, workspace sets `rate=200`; resolved
  rate is `200`.
- `test_notebook_override_wins_over_workspace` — same shape,
  one more layer.
- `test_tombstone_suppresses_lower_layer` — defaults ship
  `voice.robot`; user layer adds `{"id": "voice.robot",
  "deleted": true}`; resolved bank has no `voice.robot`.
- `test_add_only_layer_injects_new_record` — workspace adds a
  brand-new cue `sound.deploy_success`; it appears in the
  resolved bank even though no lower layer mentions it.
- `test_resolve_is_pure` — resolver must not mutate any input
  layer.

**Docs touch points.** `docs/WORKSPACES.md` "Settings cascade"
chapter with the four-layer diagram and worked examples.
`USER_MANUAL.md` settings editor section gains a "which layer
am I editing?" note. `docs/AUDIO.md` cross-links from its
"bank loading" section to the cascade doc.

---

## F53 — Per-tab focus memory across tab and window switches

**Gap.** Once tabs land (F29), switching between them with
`Ctrl+Tab` will re-enter whichever tab the user focused — but
there is no specification of *where inside* that tab the
keyboard lands. The naive implementation resets focus to the
last cell of the target tab (or to whatever field was focused
globally before the switch), which means the user loses their
editing spot every time they context-switch. Same issue applies
when returning to ASAT after an OS-level Alt+Tab.

**Where it surfaces.** A blind developer editing cell 4 in
notebook A jumps to notebook B to check output, Ctrl+Tabs back,
and expects to resume typing in cell 4 at the exact column they
left. Without focus memory they land on cell 11 (the last cell)
in NOTEBOOK mode with no cursor context and have to re-navigate.
This is the concrete UX concern the user originally raised when
sketching the workspace/tab model.

**Sketch.** Every `NotebookTab` owns a `FocusSnapshot`:

```python
@dataclass
class FocusSnapshot:
    focus_mode: FocusMode              # NOTEBOOK / INPUT / OUTPUT / SETTINGS / MENU
    cell_index: int | None             # which cell was focused
    cursor_column: int | None          # INPUT mode: cursor position within buffer
    output_line: int | None            # OUTPUT mode: output cursor line
    output_column: int | None          # OUTPUT mode: output cursor column
    updated_at: datetime               # for observability, not logic
```

Capture and restore rules:

- **Capture** happens every time a focus-affecting event fires
  (`FOCUS_MODE_CHANGED`, `CELL_FOCUS_CHANGED`,
  `INPUT_CURSOR_MOVED`, `OUTPUT_CURSOR_MOVED`). A
  `FocusMemoryWatcher` subscribes to these and updates the
  active tab's `FocusSnapshot` in place.
- **Restore** happens on `TAB_FOCUSED`. `FocusMemoryWatcher`
  reads the target tab's snapshot and drives
  `NotebookCursor.focus_cell(index)` +
  `InputRouter.set_focus_mode(mode)` +
  `InputRouter.set_cursor_column(col)` in that order. Each of
  those setters already exists or is a small addition.
- **Narration** on restore: "tab 2 of 3, notebook 'deploy', cell
  4 of 9, input mode, column 12". The richness is configurable
  via F31 verbosity presets; the minimum announcement is just
  the tab label.

**Edge cases to nail down in code and tests.**

1. *Snapshot references a cell that has been deleted.* On
   restore, clamp `cell_index` to the nearest valid index (or
   the last cell if the notebook is empty after trimming). Fire
   `FOCUS_SNAPSHOT_CLAMPED` so the user is told: "cell 4 no
   longer exists; focused cell 3".
2. *Snapshot's focus_mode was SETTINGS / MENU.* Modal surfaces
   are not per-tab; always restore to NOTEBOOK mode on
   tab switch, independent of the captured mode. Settings and
   menu are window-level, not tab-level.
3. *Tab was never focused before.* A freshly opened tab has
   `FocusSnapshot(focus_mode=NOTEBOOK, cell_index=0, cursor_column=0,
   output_line=None, output_column=None)` by default.
4. *Return from OS-level Alt+Tab.* The ASAT process has no
   reliable "window gained focus" signal on POSIX terminals.
   Don't attempt to detect it; the internal snapshot is enough
   because nothing changes focus inside ASAT while the terminal
   is backgrounded. (If a terminal *does* deliver focus-in/out
   via xterm's `?1004h` protocol, subscribe to it as a small
   bonus, but don't make correctness depend on it.)

**Module surface.** Introduce `asat/focus_memory.py`:

- `FocusSnapshot` dataclass.
- `FocusMemoryWatcher` — subscribes to the focus-affecting
  event family; writes to `NotebookTab.focus_snapshot`; on
  `TAB_FOCUSED`, reads and drives restoration. Follows the
  SOURCE class-attribute convention (`SOURCE = "focus_memory"`).
- A small `restore_focus(tab: NotebookTab, router:
  InputRouter) -> RestoreOutcome` pure helper so restoration
  logic is testable without a full Application wire-up.

**Events.**
- `TAB_FOCUSED` (payload: `tab_index`, `tab_id`) — published
  by the TabBar from F29.
- `FOCUS_SNAPSHOT_UPDATED` (debug-level) — for F22 diagnostic
  log only; not used by any production subscriber.
- `FOCUS_SNAPSHOT_CLAMPED` — payload `{requested_index,
  actual_index}`; consumed by a narration binding.

**Tests.** `tests/test_focus_memory.py`:

- `test_snapshot_captures_input_cursor_column`
- `test_tab_switch_restores_cell_and_column`
- `test_deleted_cell_clamps_to_valid_index_and_narrates`
- `test_settings_mode_does_not_contaminate_tab_snapshot`
- `test_fresh_tab_defaults_to_notebook_mode_at_cell_zero`
- `test_output_mode_snapshot_restores_output_cursor`

**Docs touch points.** `docs/USER_MANUAL.md` gains a
"Switching tabs" section describing the restoration guarantee.
`docs/EVENTS.md` registers the three new event types.
`docs/WORKSPACES.md` cross-links focus memory as part of the
tab lifecycle.

---

## F54 — Workspace CLI discovery + `:workspace` meta-commands

**Status (2026-04-19).** A minimal slice has shipped:
`asat <dir>`, `asat <dir> <name>`, `asat <file.asatnb>`, and
`asat --init-workspace <dir>` all open / create a workspace and
pick a default notebook (last-opened pointer, single existing,
or fresh `default.asatnb`). Three meta-commands are live:
`:workspace` re-announces the project root, `:list-notebooks`
narrates every notebook by name, `:new-notebook <name>` writes
a fresh file (the user must restart ASAT to open it; in-flight
tab switching is deferred). Recent-workspaces history,
`--list-workspaces`, in-process `:workspace open`, and the
dirty-save / Y/N bootstrap prompt remain on this card.

**Gap.** Once F50/F51 give workspaces and notebook files a shape
on disk, ASAT still needs a way for a human to open one.
Today's launcher is `python -m asat` with no positional
argument; there is no `:workspace`, no "recent workspaces",
and no concept of binding a launch to a specific directory.

**Where it surfaces.** "How do I open last week's workspace?"
has no answer. A user cannot script `asat ~/projects/deploy` to
resume work, and there is no in-session way to switch to a
different workspace without killing and relaunching the
process.

**Sketch.** Two layers: a CLI surface and a meta-command
surface. Both route through the same `Application.open_workspace(path)`
method so behaviour is identical regardless of entry point.

**CLI forms.**

```
asat                                 # fall back to default workspace
asat <directory>                     # bind to that directory as a workspace
asat <directory>/file.asatnb         # bind to parent directory, focus this notebook
asat --new-workspace <directory>     # bootstrap a fresh workspace at that path
asat --list-workspaces               # print recent workspaces with last-opened timestamps
```

Resolution rules for `asat <path>`:

1. If `path` is a file with `.asatnb` extension: the workspace
   is `path.parent.parent` if that parent contains a
   `.asat-workspace` marker, else `path.parent` bootstrapped as
   a workspace. Open the file as the focused tab.
2. If `path` is a directory with `.asat-workspace` marker: open
   as workspace.
3. If `path` is a directory *without* the marker: prompt (via
   narration + Y/N key) "bootstrap as new ASAT workspace?"
   unless `--new-workspace` is passed, which silently
   bootstraps.
4. If `path` does not exist: fail with a clear error; do not
   auto-create deep directory trees.

**Default workspace.** `asat` with no args opens
`$ASAT_HOME/default-workspace/` (bootstrapping it on first
launch). This preserves the current no-args-launches-something
behaviour while making the on-disk shape explicit.

**Recent workspaces.** `$ASAT_HOME/recent-workspaces.json` —
top-level list, most-recent-first, capped at 25 entries, each
entry `{path, opened_at, notebook_count}`. Updated on every
`WORKSPACE_OPENED`. Used by `--list-workspaces` and by a
future F54b "recent" picker (out of scope for F54 v1).

**Meta-commands.** Add to F17's meta-command router:

- `:workspace` — print the current workspace path.
- `:workspace open <path>` — close current workspace (prompt to
  save dirty notebooks) and open the given path. Equivalent to
  quit + relaunch, but in-process.
- `:workspace bootstrap <path>` — create a new workspace at
  `<path>` without opening it.
- `:workspace recent` — narrate the five most-recent
  workspaces with their last-opened timestamps.
- `:new-notebook [<slug>]` — create a fresh notebook in the
  current workspace, open it in a new tab. Slug optional (auto
  from timestamp if missing).
- `:open-notebook <slug>` — open the named notebook in a new
  tab; no-op if already open (focuses the existing tab instead).
- `:close-tab [!]` — close the focused tab. `!` suffix skips
  the dirty-save confirmation.
- `:save` / `:save-as <slug>` — persist the focused notebook.
- `:rename-notebook <new-slug>` — rename the focused notebook
  on disk; `notebook_id` is stable across the rename so F55's
  `window.json` still resolves the reference.

Every meta-command above also gets a keybinding surface
elsewhere (Ctrl+T for new tab, Ctrl+W for close tab, Ctrl+S for
save, etc.) but the meta-commands are the discoverable,
scriptable contract.

**Error narration.** A bad `:workspace open` path must narrate
the exact failure mode ("no .asat-workspace marker — pass
--new-workspace or use :workspace bootstrap"), not just "error".

**Tests.** `tests/test_workspace_cli.py`:

- `test_no_args_opens_default_workspace_bootstrapping_on_first_run`
- `test_directory_arg_with_marker_opens_as_workspace`
- `test_directory_arg_without_marker_prompts_then_bootstraps`
- `test_file_arg_walks_up_to_find_workspace`
- `test_nonexistent_path_fails_cleanly`
- `test_recent_workspaces_json_rotates_on_open`

`tests/test_meta_commands_workspace.py`:

- `test_workspace_open_saves_dirty_notebooks_first`
- `test_new_notebook_appears_in_tab_bar`
- `test_close_tab_with_bang_skips_save_prompt`
- `test_rename_preserves_notebook_id`

**Docs touch points.** `USER_MANUAL.md` gets a "Launching ASAT"
rewrite: old paragraphs about the default bank path move into
a "Default workspace" section. `docs/WORKSPACES.md`
"Command-line surface" chapter lists every form. `docs/EVENTS.md`
adds `WORKSPACE_OPENED` / `WORKSPACE_CLOSED` to the table.

---

## F55 — Persistent window state (`window.json`)

**Gap.** Even after F50 (workspace), F51 (notebook files), and
F29 (tabs in memory), relaunching ASAT on a workspace still
opens a single empty tab. The set of "which notebooks were
open, in what order, with which one focused, and at what
snapshot inside each" is discarded on shutdown. The user has
to reopen every tab by hand every time.

**Where it surfaces.** A user who closes ASAT at end-of-day
with three tabs open (deploy runbook, training scratch,
postmortem draft) and reopens the workspace in the morning
expects all three tabs restored in the same order, with the
same tab focused. Today they get one blank tab.

**Sketch.** A single JSON file,
`<workspace>/settings/window.json`, holds the serialised window
state:

```json
{
  "schema_version": 1,
  "saved_at": "2026-04-19T17:10:00Z",
  "tabs": [
    {
      "notebook_id": "nb-01HZS4VXTW...",
      "notebook_path": "notebooks/deploy.asatnb",
      "focus_snapshot": {
        "focus_mode": "INPUT",
        "cell_index": 4,
        "cursor_column": 12,
        "output_line": null,
        "output_column": null
      },
      "pinned": false
    },
    { "notebook_id": "nb-01HZS4W1AB...", ... }
  ],
  "focus_index": 1,
  "settings_editor_layer": "workspace"
}
```

Field rules:

- `tabs[]` is ordered; the TabBar renders them in this order
  on restore. Order is mutated by keyboard reorder gestures
  (Ctrl+Shift+PageUp/Down in a future entry) and flushed on
  change.
- `tabs[].notebook_id` is the primary reference. `notebook_path`
  is a hint for the fast path; if it no longer resolves, the
  loader falls back to scanning `notebooks/` for a file whose
  JSON `notebook_id` matches. If neither resolves, the tab is
  dropped and a `TAB_RESTORE_FAILED` event names the missing
  ID so the sound bank can cue a warning.
- `focus_snapshot` is the F53 snapshot type, serialised
  directly. Restore uses F53's `restore_focus` helper.
- `pinned` is reserved for a future "pin tab" feature; persist
  and restore as-is, don't act on it yet.
- `focus_index` must point at a valid index after restoration;
  if out of range (because a tab was dropped), clamp to the
  last tab and narrate.
- `settings_editor_layer` remembers which layer (F52) the user
  was last editing, so reopening the editor lands them in the
  same context.

**Save triggers.** Window state must be debounced like F51's
idle save policy — but with a *hard* flush on every tab-level
event (`TAB_OPENED`, `TAB_CLOSED`, `TAB_FOCUSED`, `TAB_REORDERED`).
Focus-snapshot updates inside a tab only need the debounced
flush; losing a few cursor columns to a crash is acceptable,
losing "which tab was open" is not. A small `WindowStatePersister`
handles both paths.

Atomic write: same pattern as F51 (temp file + rename).

**Startup restoration order.**

1. `Application` resolves the workspace (F50).
2. Read `settings/window.json`; if missing or unparseable,
   proceed with a fresh single-tab window on the last
   modified notebook in `notebooks/` (or a new empty notebook
   if the directory is empty). Log the failure path via F22.
3. For each entry in `tabs[]`: load the notebook (F51), build
   a `NotebookTab`, restore its `FocusSnapshot`.
4. Apply `focus_index` clamp.
5. Publish `WINDOW_RESTORED` (payload: tab count, focus index,
   dropped tab ids).
6. Narrate: "workspace 'deploy' opened, 3 tabs restored, tab 2
   focused, notebook 'training', input mode, cell 4 column 12".

**Migration.** Absent `window.json` = fresh window, no
migration needed. `schema_version` bump triggers an explicit
migration path; v1 → v2 would preserve the fields we have and
fill new ones with defaults.

**Interaction with F53.** F53 provides the in-memory focus
snapshot. F55 serialises it. The two features must ship in
that order (F53 first) because F55 has nothing to persist
without F53 populating it.

**Tests.** `tests/test_window_state.py`:

- `test_roundtrip_preserves_all_tabs_in_order`
- `test_focus_index_clamped_when_tabs_dropped`
- `test_missing_notebook_file_narrates_and_drops_tab`
- `test_corrupted_json_falls_back_to_fresh_window`
- `test_debounced_save_not_more_than_once_per_window`
- `test_tab_level_events_flush_immediately`
- `test_atomic_write_survives_mid_save_crash`

**Docs touch points.** `docs/WORKSPACES.md` "Window state"
chapter with the JSON schema and startup restoration flow.
`docs/EVENTS.md` gains `WINDOW_RESTORED`, `TAB_RESTORE_FAILED`,
and the `TAB_*` family. `USER_MANUAL.md` updates the
"Launching ASAT" section to note that open tabs persist across
sessions.

---

## Cluster: macro record, replay, and scenario automation (F56 – F59)

F56 – F59 form one proposal, not four independent features. They
share a single file format, a single replay engine, and the same
execution mode. Land them in order: each entry builds strictly
on the previous one's data structures, and implementing a later
entry first would force a rewrite.

Single-sentence vision: **ASAT records any live workflow —
keystrokes, kernel results, and bus events — into a JSONL file,
then replays that file either as a deterministic regression
scenario (for tests and the smoke suite) or as a user-facing
macro with variable prompts and result-based branching (for
everyday automation of workflows that are routine but not
mechanical).**

The cluster is deliberately split so the regression-test value
ships in two PRs (F56 + F57) before the user-facing automation
story (F58 + F59). The codebase gets its anti-drift guard early;
end users get power tools once the engine is proven.

**Cross-references** (keep linked as you work):

- **F22** (diagnostic JSONL logger, shipped) already captures
  every event in the format F56 adopts. The recorder reuses the
  same encoder; only the event-type filter differs.
- **F25** (user-remappable keybindings, open) must be respected
  by the recorder — store `action` names, not raw keys, for
  anything bound to a router action so macros survive rebinds.
- **F50** (workspace directory, open) defines where macros live
  on disk (`<workspace>/macros/*.asatmacro.jsonl`). Until F50
  ships, macros land at `~/.asat/macros/`.
- **F39** (event log viewer, open) gives a natural UI for
  browsing recorded macros; the two features share a reader.
- **SMOKE_TEST.md** and `tests/test_smoke_scenarios.py` are the
  first customers of F57 — once F57 lands, the scenario file
  gets replaced by a `tests/fixtures/macros/smoke.asatmacro.jsonl`
  replayed through the scenario harness.

**Recommended landing order.**

1. **F56** — recorder + replayer + file format + MACRO_PLAYBACK
   mode. Nothing user-visible beyond "you can record, you can
   play back verbatim".
2. **F57** — `expect` step type + scenario harness. Regression
   tests can now be recorded instead of hand-written.
3. **F58** — `{{var}}` templating + `prompt` steps. First
   genuinely user-useful macros (e.g. "deploy to $env").
4. **F59** — `if` step with condition evaluated against captured
   state. Macros react to earlier computation results.

Each entry below is implementation-ready: file layouts, JSON
schemas, key bindings, events, tests. They are written to be
picked up months from now by someone (possibly Claude) who no
longer has the cluster in short-term context.

---

## F56 — Macro recorder, replayer, and file format

**Gap.** ASAT has no way to capture a live workflow as a
reusable artifact. The user can type commands, navigate, edit
settings — and the only trace left behind is the diagnostic log
(F22), which is a one-way stream with no replay semantics.
`tests/test_smoke_scenarios.py` shows the shape of what would be
valuable — scripted keystrokes + asserted event sequence — but
every such scenario is hand-coded today. There is no artifact a
user could produce during normal use and hand to a test, a
collaborator, or a later self.

**Where it surfaces.** Three distinct pain points share one
root cause:

1. **Regression drift.** SMOKE_TEST.md documents 9 acts of
   expected behaviour; test_smoke_scenarios.py encodes 20
   assertions against that behaviour. If a future PR quietly
   changes the event order during a successful command, the
   test fires — but only because someone had to write it. No
   blind user can file "my workflow from yesterday broke today"
   as a reproducible bug without screen recording.
2. **Repetitive workflows.** A blind SRE who runs
   `kubectl -n prod get pods | grep foo | ...` forty times a
   week has no way to bind that sequence to one keystroke.
   Shell aliases cover the single-command case but not the
   multi-cell narration-paced ones.
3. **Learning + onboarding.** A new user who wants to see
   "what does a typical debugging session sound like?" cannot
   replay a reference session — the existing `:welcome` tour is
   a canned narration, not a real transcript.

**Sketch.** Introduce `asat/macros/` as a new package with three
modules: `recorder.py`, `replayer.py`, and `format.py`. Add one
new `FocusMode` (MACRO_PLAYBACK), one meta-command family
(`:record`, `:play`, `:macros`), and one settings section
(`macros`).

### File format (`format.py`)

JSONL, one step per line, first line is an envelope:

```
{"schema_version": 1, "recorded_at": "2026-04-19T12:34:56Z", "asat_version": "0.x", "name": "deploy-to-staging", "description": "optional human text"}
{"step": 1, "kind": "key", "key": {"name": "escape"}, "focus_mode": "input"}
{"step": 2, "kind": "action", "action": "open_settings", "focus_mode": "notebook"}
{"step": 3, "kind": "text", "text": "echo hello", "focus_mode": "input"}
{"step": 4, "kind": "submit"}
```

Step kinds (F56 defines four; F57-F59 add more):

- `key` — a single keystroke that maps to no router action
  (e.g. arrow keys inside the settings composer). `key` is a
  serialised `Key`: `{"name": "up", "modifiers": ["ctrl"]}`.
- `action` — a router action name (`"open_settings"`,
  `"move_up"`, …). Recorded in preference to `key` whenever the
  router would dispatch the keystroke to a named action, so
  remapping (F25) does not invalidate the macro.
- `text` — a printable-character run typed into INPUT mode.
  Stored as a string so it is visible and editable by hand.
- `submit` — Enter in INPUT mode that enqueued a cell. Distinct
  from a raw Enter `key` step so the replayer knows to call
  `drain_pending` + `execute`.

Every step also carries `focus_mode` (advisory — for diagnostics
and to let the replayer refuse to run a macro started in the
wrong mode).

`format.py` exposes `read(path) -> Macro`, `write(path, macro)`,
and `Macro` as a frozen dataclass holding `envelope: dict`,
`steps: tuple[Step, ...]`. Unknown step kinds in a newer-schema
file are passed through opaquely so a v1 replayer on a v2 file
can refuse cleanly with "this macro was recorded by a newer
ASAT".

### Recorder (`recorder.py`)

`Recorder(bus, session, cell_id_policy)` subscribes to the bus
with `WILDCARD` (same mechanism as `JsonlEventLogger`) and keeps
an in-memory list of steps. Triggers:

- `KEY_PRESSED` → if the same dispatch cycle produced an
  `ACTION_INVOKED`, collapse both into one `action` step. If
  not, emit a `key` step.
- Consecutive printable-key events in INPUT mode are collapsed
  into a single `text` step so a 40-character command does not
  produce 40 lines of JSON.
- `COMMAND_SUBMITTED` → `submit` step (replacing the Enter
  `key` step that preceded it, to keep the macro readable).

`recorder.stop()` publishes `MACRO_RECORDING_STOPPED` and
returns a `Macro` ready to serialise. Save path resolves through
the workspace (or `~/.asat/macros/`) with a slug derived from
the `:record <name>` argument.

### Replayer (`replayer.py`)

`Replayer(app, macro)` owns the replay state machine. Keys are
injected into `app.handle_key` in order; `submit` steps also
call `drain_pending` + `execute`. Between steps, the replayer:

- publishes `MACRO_STEP_EXECUTED` with `{step_index, kind,
  elapsed_ms}`;
- yields control to the bus so narrations and audio land in
  order before the next step;
- observes `FOCUS_CHANGED` to confirm the advisory `focus_mode`
  matches (mismatch → narrate warning, continue).

Replayer runs on a timer (default 100 ms between steps,
configurable per-macro) so narration has time to speak. During
playback the app enters `FocusMode.MACRO_PLAYBACK`; Escape
aborts (publishes `MACRO_ABORTED`), any other key is swallowed
with a narrated "macro playing — press Escape to abort" hint.

### User-facing meta-commands

- `:record <name>` — start recording. Narrates "recording
  `<name>`; type `:record stop` to finish". The record step
  itself is *not* captured (metacommands with a `:record`
  prefix are filtered).
- `:record stop` — finish recording, save to disk, narrate
  path. Opens a confirmation sub-mode before overwriting an
  existing macro.
- `:record abort` — finish recording, discard.
- `:play <name>` — replay the macro. Runs a confirmation prompt
  first (*"play `<name>`? 12 steps. Enter to confirm, Escape to
  cancel"*) which can be disabled per-macro in settings.
- `:macros` — list every saved macro with name, step count,
  recorded-at date.

### Focus mode: MACRO_PLAYBACK

A peer of NOTEBOOK/INPUT/OUTPUT/SETTINGS, mutually exclusive
with them (same rule the existing modes follow). Adds a row to
the `FocusMode` enum, a branch to the `InputRouter` dispatch
table (one binding: Escape → `macro_abort`), and a new settings
section (see below).

### Settings

`macros` section with one record per saved macro, fields:

- `enabled` (bool) — can `:play <name>` run it?
- `confirm_before_replay` (bool, default true)
- `announce_steps` (bool, default true) — read "step 3 of 12"
  before each step? Off for tight loops that would talk over
  themselves.
- `step_interval_ms` (int, default 100)
- `description` (str, round-tripped from the envelope)

### Events

- `MACRO_RECORDING_STARTED` — `{name}`
- `MACRO_RECORDING_STOPPED` — `{name, step_count, path}`
- `MACRO_SAVED` — `{name, path}` (distinct from stopped, so
  save errors narrate separately)
- `MACRO_STARTED` — `{name, step_count}`
- `MACRO_STEP_EXECUTED` — `{step_index, kind, total, elapsed_ms}`
- `MACRO_COMPLETED` — `{name, step_count, duration_ms}`
- `MACRO_ABORTED` — `{name, reason, step_index}`

All seven bind to default-bank narrations with terse templates
so audio reaches the user through the existing SoundEngine
pipeline. `say_template` values follow the conventions in
`docs/DEVELOPER_GUIDE.md`.

### Determinism guarantees

- Recording captures *actions* in preference to *keys*, so a
  macro recorded on one user's rebound keyboard replays
  faithfully on another's default binding.
- Printable text is captured as the character, not the
  keycode, so locale-shifted punctuation replays identically.
- Schema version in the envelope enables cross-version refusal
  without silent breakage: a v1 replayer on a v2 file narrates
  *"macro recorded by ASAT $ver; please update"* and stops.
- Timestamps in steps are advisory only; replay paces by the
  `step_interval_ms` setting, not by wall-clock recordings, so
  macros recorded on a slow TTY replay cleanly on a fast one.

### Safety

- A macro cannot enter record sub-mode while a macro is
  already playing.
- A macro cannot `:play` itself (recursion guard — trap during
  load).
- Playback is gated behind a confirmation prompt unless the
  user opted out per-macro.
- `Ctrl+C` in the host terminal aborts playback; the replayer
  handles `KeyboardInterrupt` by publishing `MACRO_ABORTED`
  with `reason="interrupted"`.

### Tests (`tests/test_macros.py`)

- `test_format_roundtrip_preserves_all_step_kinds`
- `test_recorder_collapses_printable_run_into_text_step`
- `test_recorder_prefers_action_name_over_raw_key`
- `test_replayer_injects_keys_in_recorded_order`
- `test_replayer_publishes_step_events_in_order`
- `test_replayer_refuses_newer_schema_version`
- `test_replayer_escape_publishes_aborted`
- `test_macro_cannot_play_itself_recursively`
- `test_confirm_prompt_blocks_default_playback`
- `test_recording_survives_rebound_keys_via_action_names` —
  rebind Ctrl+O, record a macro, rebind it back, replay; the
  action fires.
- Drift guard: `test_events_docs_sync` picks up the seven new
  event types automatically.

### Docs touch points

- `docs/USER_MANUAL.md` — new "Macros" chapter.
- `docs/CHEAT_SHEET.md` — new rows in the meta-commands table
  for `:record`, `:record stop`, `:record abort`, `:play`,
  `:macros`. MACRO_PLAYBACK gets a mode row.
- `docs/EVENTS.md` — seven new event rows.
- `docs/DEVELOPER_GUIDE.md` — one paragraph on how to add a
  new step kind (dispatch table in `replayer.py`).

---

## F57 — Scenario assertion DSL (replay as regression test)

**Depends on F56.**

**Gap.** F56 lets a user record a workflow and replay it
verbatim, but the replay engine has no way to check that the
*outcomes* are the same as they were at record time. Today,
`tests/test_smoke_scenarios.py` is the only file that exercises
end-to-end keystroke → event → assertion chains, and every
assertion is hand-written Python. If the author of a future PR
changes the order in which `COMMAND_STARTED` and `OUTPUT_CHUNK`
fire, they either know to hand-write a new test or the smoke
scenario goes stale silently. Worse, a real user who recorded
"this session worked yesterday; it does not today" cannot
replay the recording as a test — they can only replay it and
see whether the audio sounds the same.

**Where it surfaces.** Three concrete pain points:

1. **SMOKE_TEST.md drift.** The doc describes 9 acts. The
   Python test file mirrors them. Both drift independently —
   the pre-test accuracy pass in PR #54 fixed six mismatches
   that had accumulated over six PRs. An asserting macro
   replaced by a single fixture file collapses the drift to
   one source of truth.
2. **Reproducing user bugs.** A blind SRE who notices "after
   `:state` in OUTPUT mode, the narration now says `line 3`
   when it used to say `line 4`" has no low-effort way to hand
   that observation to a maintainer. With F57 they file
   *"here is a 20-step macro; step 14's expected `focus.line`
   payload is `4`; today I get `3`"*.
3. **Maintainer confidence.** A contributor touching the
   router can replay the shipped smoke macro locally and get
   "pass / fail at step N" feedback, not a wall of pytest
   tracebacks.

**Sketch.** Extend the F56 file format with a single new step
kind — `expect` — and a tiny scenario harness that wraps the
F56 replayer so tests and `:play --assert` share one engine.

### Step kind: `expect`

```
{"step": 14, "kind": "expect", "event_type": "output.line.focused", "where": "next", "match": {"line_number": 4}}
```

Fields:

- `event_type` — one of `EventType.value`. Required.
- `where` — `next` (the next event of this type after the prior
  step fires), `any` (anywhere in the window between this
  `expect` and the next), or `last` (the most recent instance).
  Default `next`. Three values only; resist the urge to add
  more until a real use case demands it.
- `match` — an object whose keys are payload field paths
  (dotted), whose values are either plain values (equality) or
  the special strings `"<ANY>"` (field must exist, any value),
  `"<ABSENT>"` (field must not exist), `"<NONEMPTY>"` (string
  or list must be truthy). Equality matching is strict (`0` is
  not `False`, `"1"` is not `1`).
- `timeout_ms` — how long to wait for the event. Default
  inherits the macro's `step_interval_ms`. Rare override for
  genuinely slow steps.

Failure mode: the replayer publishes
`MACRO_STEP_ASSERTION_FAILED` with `{step_index, expected,
observed}` and (by default) aborts the macro. A `continue_on_fail`
flag in the envelope flips this to "log and keep going" —
useful for the SRE use case where you want the full list of
discrepancies, not just the first.

### Scenario harness (`asat/macros/scenario.py`)

Extract the fixture already in
`tests/test_smoke_scenarios.py::_ScenarioFixture` into a
shared helper so both unittest code and `:play --assert` share
one implementation. Public API:

```python
class ScenarioHarness:
    def __init__(self, app: Application) -> None: ...
    def run(self, macro: Macro) -> ScenarioResult: ...

@dataclass(frozen=True)
class ScenarioResult:
    passed: bool
    step_count: int
    failures: tuple[AssertionFailure, ...]
    elapsed_ms: int
```

`ScenarioResult.failures` is a tuple of structured failures so
a test can `self.assertEqual(result.failures, ())` for a clean
pass-or-fail assertion. A CLI caller can pretty-print each
failure with step index and the diff between expected and
observed.

### CLI: `:play --assert`

Add an optional `--assert` argument to `:play <name>`. Without
it, `:play` is the F56 verbatim replay. With it, the replayer
enforces every `expect` step and narrates per-step pass/fail.
The argument threads through the meta-command parser as a
keyword (`{"assert": True}`) — resist introducing a general
flag-parser until a second flag appears.

### Smoke macro fixture

Once F57 lands, replace `tests/test_smoke_scenarios.py` with:

- `tests/fixtures/macros/smoke.asatmacro.jsonl` — the 9-act
  walkthrough recorded once and checked in, with `expect`
  steps between every keystroke pair.
- `tests/test_smoke_scenarios.py` (rewritten, ~50 lines) —
  loads the fixture, runs it through `ScenarioHarness`,
  asserts `passed == True`.

Keep the old class-per-act unittest hierarchy available behind
`tests/legacy_test_smoke_scenarios.py` for one release so
bisecting across the transition stays possible, then delete.

### Drift guard

`tests/test_macro_fixtures.py` enumerates every `.asatmacro.jsonl`
under `tests/fixtures/macros/` and replays it. A new fixture
dropped into the directory is automatically picked up — no
Python test file needs editing to cover a new scenario. This
is the payoff: bug reports that include a macro file become
drop-in regression tests.

### Stretch goal: generate SMOKE_TEST.md from the macro

A small `scripts/regen_smoke_doc.py` walks the smoke macro and
emits the Markdown walkthrough currently hand-maintained in
`docs/SMOKE_TEST.md`. Step kinds map to sentence templates:
`text` → "Type `<text>`"; `action` → "Press <keybinding for
<action>>"; `submit` → "Press Enter"; `expect` → "Expected: …".
Doc and fixture can no longer drift. Cross-reference in
DEVELOPER_GUIDE.md: "to change the smoke walkthrough, edit the
macro fixture, then regenerate the doc".

### Events

- `MACRO_STEP_ASSERTION_FAILED` — `{step_index, event_type,
  expected, observed}`
- `MACRO_ASSERTION_PASSED` — `{step_index}` (optional;
  `announce_steps` gates audio narration)

Both bind to default-bank narrations: failed → alert voice,
overhead + right spatial hint; passed → soft tick, centre.

### Tests (`tests/test_macros.py`, extending F56's file)

- `test_expect_next_matches_strict_equality`
- `test_expect_absent_field_match`
- `test_expect_any_field_match`
- `test_expect_fails_on_mismatch_and_aborts_by_default`
- `test_expect_continue_on_fail_collects_all_failures`
- `test_scenario_harness_returns_step_count_and_elapsed`
- `test_play_assert_narrates_per_step_outcomes`
- `test_smoke_macro_fixture_passes_end_to_end` (lives in
  `tests/test_macro_fixtures.py`; becomes the primary smoke
  test once F57 ships)

### Docs touch points

- `docs/DEVELOPER_GUIDE.md` — "Writing regression tests as
  macros" section explaining the drop-a-macro-into-fixtures
  workflow.
- `docs/USER_MANUAL.md` — one paragraph under Macros on
  `:play --assert` and why a user would use it.
- `docs/CHEAT_SHEET.md` — `--assert` on the `:play` row.
- `docs/EVENTS.md` — two new rows.
- `docs/SMOKE_TEST.md` — header note pointing at the fixture
  as the source of truth (even before auto-generation lands).

### Determinism

`expect` steps are evaluated against the in-process event bus,
not against timing. A slow machine adds latency between steps
but never changes the sequence of events observed, so the
same fixture passes on a laptop and on CI.

### Design tradeoffs deliberately rejected

- **No regex in `match`.** Keeps the format
  copy-paste-debuggable. If a user needs fuzzy matching, they
  hand-edit a single `match` into a `"<ANY>"` marker.
- **No negative assertions (`expect.not`).** The `continue_on_fail`
  mode plus a post-replay check of the event log covers the
  rare "this event must not fire" case without adding a step
  kind.
- **No cross-step variables yet.** F58 introduces `{{var}}`
  templating, and only then do `expect` matches benefit from
  variable substitution. F57 deliberately ships first with
  literal-only `match` so the harness stays small.

---

## F58 — Macro templating: variables, prompts, and captures

**Depends on F56 + F57.**

**Gap.** A verbatim macro is brittle: record *"deploy to
staging"* today and the commit hash is burned into the
`text` step forever. F56's replay is useful for
deterministic UI workflows but useless for the real
automation target — "do this common thing, but fill in
today's value for X". Without parameterisation, the user's
only recourse is editing the JSONL by hand before each run,
which defeats the point of a recorded macro.

**Where it surfaces.** The three workflows that motivated
the macro feature in the first place all need variables:

1. **"Deploy foo to env"** — the env name varies each run.
   Today the user would hand-edit the command cell.
2. **"Find the failing test and open its log"** — the test
   name is the output of the previous cell. The macro has to
   *read* that output and *use* it in the next step.
3. **"Bump version and publish"** — the version bump is
   computed from the current tag (capture) *plus* a user
   choice of patch/minor/major (prompt).

**Sketch.** Introduce three new step kinds (`prompt`,
`capture`, and a modifier on `text`/`expect` for
substitution), a `{{var}}` substitution syntax, a small set
of built-in variables, and one new event
(`MACRO_PAUSED_FOR_PROMPT`).

### Substitution syntax

`{{name}}` marks a substitution slot. Resolved at **step
execution time**, not at load time, so a `capture` in step
3 can feed a `text` step in step 5. Unknown variables
**abort the macro** with `MACRO_STEP_ASSERTION_FAILED`
carrying `reason="undefined_variable"` and the variable
name — never silently substitute an empty string.

Escape: `\{\{literal\}\}` if a user genuinely needs the two
braces in output. Double braces in recorded text are so
rare the escape lands only as an addendum in docs; the
recorder does not proactively escape.

Substitution applies to:

- `text` step `text` field
- `expect` step `match` values (not keys — match paths stay
  literal)
- `prompt` step `question` and `default`
- `capture` step `from` expression (see below)

Substitution does **not** apply to:

- `action` names (those are a closed set)
- `key.name` fields (likewise)
- `event_type` in `expect` (closed set)
- `schema_version` / envelope fields

### Step kind: `prompt`

Pause the macro, ask the user for input, bind the answer
to a variable:

```
{"step": 4, "kind": "prompt", "var": "env", "question": "deploy to which env?", "default": "staging", "choices": ["staging", "prod"]}
```

Fields:

- `var` — variable name, required. Must match
  `[a-z][a-z0-9_]*` to sidestep quoting surprises.
- `question` — the spoken prompt. Templated (so
  `"deploy to {{app}}?"` works).
- `default` — optional default value (Enter accepts).
  Templated.
- `choices` — optional list of acceptable values. If set,
  the user cycles with Up/Down and commits with Enter;
  free-form typing is blocked. Narrates "choice 1 of N:
  staging". Templated per-element.
- `secret` — bool, default false. If true, echoed
  narration says "value accepted" rather than reading back
  the text. For tokens / passwords in future macros.

Prompts run in a temporary sub-mode (`MACRO_PROMPT`, peer
of `MACRO_PLAYBACK` — same mutual-exclusion rule the rest
of the app follows). Escape aborts the macro; Ctrl+R
replays the question. `MACRO_PAUSED_FOR_PROMPT` fires on
entry with `{step_index, var, question, default, choices,
secret}`; `MACRO_PROMPT_ANSWERED` fires on commit with
`{step_index, var}` (never the value, for secret safety).

### Step kind: `capture`

Bind a variable from event payload or built-in source:

```
{"step": 7, "kind": "capture", "var": "last_output", "from": "event:output.chunk.last.text"}
{"step": 8, "kind": "capture", "var": "now", "from": "builtin:clock.iso"}
{"step": 9, "kind": "capture", "var": "cwd", "from": "builtin:workspace.root"}
```

`from` is a dotted path with a leading namespace:

- `event:<event_type>.<selector>.<field>` — selector is
  `last` (most recent event of that type), `first`, or
  `nth(N)`. Field is a dotted path into the payload.
- `builtin:<key>` — see the built-in table below.
- `env:<NAME>` — OS environment variable (only with
  `settings.macros.<name>.allow_env` set per-macro; default
  off for safety).

If the source resolves to nothing, the capture fails hard
(`MACRO_STEP_ASSERTION_FAILED`, `reason="capture_empty"`)
rather than binding an empty string. A macro that needs
"empty is OK" uses a `prompt` step with an empty default
instead.

### Built-in variables

Available in any `{{$...}}` reference (leading `$`
distinguishes built-ins from user-defined vars) without
requiring a `capture` step first:

| Reference | Value |
|-----------|-------|
| `{{$workspace.root}}` | Absolute path to current workspace (F50). Empty string if no workspace bound. |
| `{{$clock.iso}}` | UTC ISO8601 of step execution. |
| `{{$clock.epoch}}` | Seconds since epoch, integer. |
| `{{$session.id}}` | Current `Session.session_id`. |
| `{{$last_cell.command}}` | Command of most recent completed cell. |
| `{{$last_cell.exit_code}}` | Exit code (int). |
| `{{$last_cell.stdout}}` | Full captured stdout. |
| `{{$last_cell.stdout.first_line}}` | Convenience. |
| `{{$last_cell.stdout.last_line}}` | Convenience. |
| `{{$focus.mode}}` | `"notebook"`, `"input"`, etc. |
| `{{$focus.cell_id}}` | Current cell's id. |

Additional built-ins land in follow-ups as genuine user
need proves them out. Resist adding "might be useful
someday" entries — each one is a support burden.

### Precedence and scope

Variables live in a single namespace per macro run.
Precedence on lookup:

1. User-defined (set by `prompt` or `capture`).
2. Built-in (`$`-prefixed).
3. Undefined → abort.

No inheritance across macros. Running `:play foo` then
`:play bar` starts `bar` with an empty namespace — a
future "macro composition" feature would introduce
scoping, but F58 deliberately refuses to design for it.

### Settings

Extend `settings.macros.<name>` with:

- `allow_env` (bool, default false) — permit `env:` source
  in `capture` steps.
- `default_vars` (object of var → value) — seed values the
  macro starts with; useful for "my usual env" cases where
  the prompt has a sensible personal default.
- `prompt_timeout_ms` (int, optional) — if set, a prompt
  auto-accepts its default after this many ms without
  input. Surfaces in narration: *"3 seconds until
  default"*.

### Events

- `MACRO_PAUSED_FOR_PROMPT` — `{step_index, var,
  question, default, choices, secret}`
- `MACRO_PROMPT_ANSWERED` — `{step_index, var}`  (never
  the answer text, for secret safety)
- `MACRO_VARIABLE_BOUND` — `{step_index, var, source}`
  (source is `"prompt" | "capture" | "default"`; no value)
- `MACRO_SUBSTITUTION_FAILED` — `{step_index, variable,
  field_path}`

### Tests (extending `tests/test_macros.py`)

- `test_substitution_resolved_at_step_time_not_load_time`
- `test_undefined_variable_aborts_macro`
- `test_prompt_step_binds_answer_to_variable`
- `test_prompt_default_accepted_on_enter`
- `test_prompt_choices_clamp_free_text`
- `test_prompt_escape_aborts_macro`
- `test_capture_from_event_last_selector`
- `test_capture_from_builtin_clock_iso`
- `test_capture_from_empty_source_aborts`
- `test_capture_from_env_refused_without_allow_env`
- `test_secret_prompt_never_narrates_value`
- `test_substitution_supports_nested_braces_via_escape`
- `test_builtin_precedence_lower_than_user_defined`

### Docs touch points

- `docs/USER_MANUAL.md` — "Macros" chapter gains a
  "Templating" section with a worked deploy example.
- `docs/CHEAT_SHEET.md` — one-line note on `{{var}}` under
  Macros; `MACRO_PROMPT` sub-mode gets a bindings row
  (Up/Down cycle choices, Enter commits, Escape aborts,
  Ctrl+R replays question).
- `docs/EVENTS.md` — four new event rows.
- `docs/DEVELOPER_GUIDE.md` — short "adding a built-in
  variable" paragraph pointing at the lookup dispatcher.

### Tradeoffs deliberately rejected

- **No expression language.** `{{price * 1.2}}` is
  tempting but turns the file into code. Users who need
  arithmetic wrap the macro around a shell command and
  capture its output.
- **No file I/O from captures.** `from: "file:/etc/hosts"`
  is a natural extension but a large attack surface. Users
  who need a file's contents run `cat` in a cell and
  capture the output.
- **No nested templating.** `{{prefix_{{kind}}}}` is
  forbidden — the substitution pass is single-shot.
  A user who really needs this composes with a `capture`
  step.
- **No regex in prompt validation.** `choices` + free-text
  covers every case that has surfaced so far.

### Interaction with F57 `expect`

An `expect` whose `match` values reference `{{var}}`
substitutes before comparing. This turns
parameter-dependent assertions into reusable scaffolds:
*"after running `deploy to {{env}}`, expect
`command.completed.payload.exit_code == 0`"*. The test
lineage collapses to one macro, one variable set per
invocation.

---

## F59 — Macro conditional branching

**Depends on F56 + F57 + F58.**

**Gap.** F56 replays a fixed sequence. F57 adds outcome
assertions. F58 adds variables. The combination still cannot
express *"if the deploy passed, run smoke tests; otherwise,
read the last 20 stderr lines"*. A macro that wants to react
to earlier results has to abort on the first mismatch and
demand the user re-run by hand — which defeats the point of
automating the workflow in the first place.

**Where it surfaces.** Every real automation workflow bigger
than three steps needs at least one conditional:

1. **Deploy pipeline.** "If `git status` is clean, push;
   otherwise, narrate the dirty files and stop."
2. **Test triage.** "If exit code is 0, narrate 'all green';
   otherwise, capture stderr tail into `$last_fail` and
   prompt for 'open in editor or skip?'"
3. **Environment switch.** "If `{{env}}` is `prod`, prompt
   for a confirmation token first; for staging, proceed
   directly."

Without F59, each of these is either a hand-maintained pair
of macros (one per branch) that the user picks manually, or
a shell pipeline that ASAT cannot narrate step-by-step.

**Sketch.** Add one new step kind — `if` — whose condition
is evaluated against the F58 variable namespace and the
event stream captured so far. No nested branches. No loops.
No `elif` chain.

### Step kind: `if`

```
{
  "step": 9,
  "kind": "if",
  "condition": {"left": "{{$last_cell.exit_code}}", "op": "eq", "right": 0},
  "then_steps": [ /* steps run when condition true */ ],
  "else_steps": [ /* steps run when false; optional */ ]
}
```

Fields:

- `condition` — an object with `left`, `op`, `right` fields.
  `left` and `right` are substituted via F58's engine
  before comparison. `op` is one of a closed set (see
  below).
- `then_steps` — non-empty list of steps. Each step is a
  full step object; step indices inside `then_steps` /
  `else_steps` use dotted notation (`9.1`, `9.2`, …) so
  abort-at-step reports stay precise.
- `else_steps` — optional list. Missing means "no-op on
  false".

### Operator set

Deliberately small. Adding operators is cheap; removing
them later breaks every macro on disk.

| `op` | Meaning | Notes |
|------|---------|-------|
| `eq` | `left == right` | Strict — `0` ≠ `False`, `"1"` ≠ `1`. Strings compared verbatim. |
| `ne` | `left != right` | Inverse of `eq`. |
| `in` | `left in right` | `right` must be a list. Useful with `choices`. |
| `contains` | `right in left` | Substring match when `left` is a string; membership when it is a list. |
| `matches` | `re.search(right, left)` | Regex only here. Keeps the rest of the format regex-free per F57's rejection. |
| `lt` / `le` / `gt` / `ge` | Numeric comparison. | Both operands coerced to `float`; failure to coerce aborts the macro (never silently compares as strings). |
| `empty` | `left in (None, "", [], {})` | `right` ignored; lets a macro react to "capture produced nothing". |
| `exit_ok` | `int(left) == 0` | Sugar for the dominant case. |

### Condition evaluation failure

If either side fails to substitute (undefined var, F58's
abort rule), the `if` step itself aborts the macro —
never silently defaults to `false`. Same policy as F58's
substitution-failure rule, so users learn one behaviour.

A `matches` regex that fails to compile also aborts —
caught at macro load time, not run time, so the error
surfaces before any steps run.

### Flat AST enforced at load time

The parser in `format.py` refuses to load a macro where
a step inside `then_steps` or `else_steps` is itself an
`if` step. Narrated: *"nested `if` at step 9.3 — flatten
with a capture + `if` pair"*. A user who truly needs a
tree of conditions records two macros and composes them
with the "launch macro from macro" helper a future
feature would introduce.

Rationale for the nesting ban:

- Keeps the step-index scheme one level deep (`9.2`, never
  `9.3.1`).
- Keeps the narration readable — "step 9.3.1 of 12" is
  cognitively costly for audio.
- The workflows motivating F59 are all guard-then-sequence
  shapes; none has demanded a tree yet.

The ban is a load-time check, not a schema constraint, so
raising the limit later is one-line change when real need
appears.

### Events

- `MACRO_BRANCH_TAKEN` — `{step_index, branch, condition}`
  where `branch` is `"then"` or `"else"`. The full
  substituted condition object lands in the payload so
  diagnostic logs explain why the branch was taken.
- `MACRO_CONDITION_FAILED` — `{step_index, reason, left,
  right, op}`. Fires when evaluation itself failed
  (undefined var, type coercion, regex compile), distinct
  from the condition being merely false.

### Interaction with F57 assertions

A branch's `then_steps` can contain `expect` steps; so can
`else_steps`. This is how a user asserts *different*
outcomes in the two paths — *"if exit 0, expect
`cell.status == COMPLETED`; if exit 1, expect a
`COMMAND_FAILED_STDERR_TAIL`"*. The scenario harness runs
exactly the branch that was taken; the other branch's
assertions are skipped (not "passed by default") and
noted in `ScenarioResult.skipped_steps`.

### Tests (extending `tests/test_macros.py`)

- `test_if_then_branch_executes_when_condition_true`
- `test_if_else_branch_executes_when_condition_false`
- `test_if_missing_else_is_noop_on_false`
- `test_if_undefined_variable_aborts_macro`
- `test_if_regex_compile_failure_aborts_at_load_time`
- `test_if_numeric_coercion_failure_aborts`
- `test_if_nested_refused_at_load_time`
- `test_branch_taken_event_includes_substituted_condition`
- `test_scenario_harness_skips_untaken_branch_assertions`
- `test_exit_ok_sugar_matches_zero_exit_code`
- `test_empty_op_treats_none_and_empty_collections_equally`

### Docs touch points

- `docs/USER_MANUAL.md` — "Macros" chapter gains a
  "Conditionals" section with the deploy + test-triage
  examples from the gap.
- `docs/DEVELOPER_GUIDE.md` — one paragraph on "adding an
  operator": table entry, evaluator dispatch, two tests.
- `docs/EVENTS.md` — two new event rows.
- `docs/CHEAT_SHEET.md` — one-line note under Macros that
  `if` steps exist; the full grammar lives in the
  USER_MANUAL.

### Tradeoffs deliberately rejected

- **No nested `if`.** Load-time refusal with migration
  hint. Revisit only after three real user requests land.
- **No loops (`for`, `while`, `repeat`).** Loop macros are
  powerful and easy to get wrong (infinite loops, fixing
  them mid-playback). A user who needs to iterate runs a
  shell `for` inside a cell and captures its output.
- **No boolean combinators (`and`, `or`, `not`).** A user
  who needs `A and B` uses two `capture` steps and a
  nested condition — one that compares a pre-computed
  `$both` variable. Cheapest workaround for the cases
  seen; revisit when a user files a concrete motivator.
- **No else-if chain.** Equivalent of guards-then-else
  expressed as a flat sequence of `if` steps that
  short-circuit by setting a `$handled` variable. Slightly
  verbose; keeps the AST flat.

---

## Cluster status: F56 – F59 at a glance

Once all four entries ship, a user can:

1. Press a keystroke to start recording (`:record deploy`).
2. Walk through a workflow as they normally would.
3. Stop recording; the JSONL file lands on disk next to
   their other macros.
4. Replay it verbatim with `:play deploy` — same
   keystrokes, same narration, same outcomes.
5. Replay it as a test with `:play deploy --assert` — any
   drift between record-time and today fires an
   `ASSERTION_FAILED` event with the diff.
6. Parameterise it by hand-editing one step into a
   `prompt` — the next run asks for env/version/etc. and
   substitutes the answer.
7. Branch on earlier results by adding an `if` step —
   deploy-if-clean, read-tail-if-failed, etc.

The format is append-only across the four PRs: every
`.asatmacro.jsonl` recorded under F56 remains playable
after F57/F58/F59 ship; new step kinds only appear when
the recorder was told to emit them.

### Future extensions after F59

Each of these is a natural follow-up but deliberately
out of scope for the initial cluster. Land only when a
user files a concrete motivator.

- **Macro composition.** A `call` step that runs another
  macro inline; variable namespace handling (shared?
  isolated? parent-visible?) is the open design question.
- **Loops.** A `foreach` step that iterates a captured
  list. Hardest safety surface — aborts on Ctrl+C, cap
  total iterations, narrate progress.
- **Record-by-replay editing.** Record a macro, replay
  it in an editing mode where the user can drop / reorder
  / modify steps live.
- **Shared macro library.** A distributable format
  (`~/.asat/macros/shared/`), signing, trust prompts.
- **Auto-generated SMOKE_TEST.md.** Already flagged as a
  stretch goal under F57; becomes much more valuable once
  F58/F59 let the macro carry templating and branches the
  doc would otherwise have to explain in prose.

---

## F60 — Persistent computational backend (shared shell / REPL)

**Status: Shipped.**

**Sketch (shipped).** POSIX hosts now launch one long-lived
`bash --norc --noprofile` at session start and route every cell's
command through it via stdin (`asat/shell_backend.py`). Sentinel
framing on both pipes preserves the stdout/stderr split (no PTY
needed), so ASAT's spatial L/R audio routing keeps working. State
carries between cells exactly as a human at a real prompt would
expect: `cd`, `export`, function definitions, shell options. A
timer-driven `os.killpg(SIGINT)` interrupts a stuck command without
killing the shell — the shell's own `trap : INT` catches SIGINT,
while `exec` resets the disposition for the foreground child so it
exits with 130. The CLI flips this on by default and falls back to
the per-cell `ProcessRunner` when bash is missing, on Windows, or
when the user passes `--no-shared-shell` (`asat/__main__.py
:_pick_runner`). The kernel surfaces a crashed shell as the
dedicated `EXIT_CODE_BACKEND_ERROR=125` so callers can distinguish
it from any user-command failure. End-to-end coverage in
`tests/test_shell_backend.py` (state persistence, timeout-without-
killing-the-shell, sentinel-prefix-mid-line) and
`tests/test_app.py::ApplicationSharedShellTests`.

**Open follow-ups.**

- **Per-session backend choice.** Today the backend is bash or
  nothing. Record `backend = "bash" | "ipython" | …` in the
  Session JSON so resumes pick the same one back up.
- **Per-cell override.** A `:backend none` meta-command (or a
  cell-level flag) for one-off invocations like `git status` where
  shared state is irrelevant or actively unwanted.
- **Restart semantics.** A `:restart` meta-command + audio cue
  ("backend exited code N — press Ctrl+R to restart") and a
  way to mark every downstream cell as "ran against a different
  process" so a future re-run does not silently inherit different
  state.
- **Windows backend.** Mirror the same protocol against `cmd /K`
  (or `pwsh -NoProfile -NoExit`), reusing the sentinel framing.
- **Non-shell kernels.** Wrap the Jupyter-kernel protocol so
  IPython, Node, etc. work the same way.
- **Security.** A long-lived shell amplifies blast radius. Today
  `--no-shared-shell` is the only escape valve; never auto-run
  cells from a loaded session against the new backend without an
  explicit prompt.

---

## F61 — Cell hierarchy: sections, folds, and grouping

**Sketch (partially shipped).** The flat `Session.cells` list now
has a polymorphic `Cell.kind: CellKind` discriminator with two
values: `COMMAND` (the existing executable cell) and `HEADING`
(an announce-only section header carrying `heading_level` 1-6 and
`heading_title`). Heading cells cannot be executed (`mark_running`
/ `update_command` guard on `is_executable`); `Application.execute`
short-circuits if the target cell is a heading. Session JSON
gained `kind` / `heading_level` / `heading_title` fields with
backward compatibility — a missing `kind` defaults to `COMMAND` so
pre-F61 sessions keep loading.

NOTEBOOK mode gained NVDA-style keystrokes for outline jumping:
`]` / `[` step to the next / previous heading of any level, and
`1`-`6` jump to the next heading of that specific level. INPUT
mode grew two meta-commands: `:heading <level> <title>` inserts a
heading cell before the next prompt, and `:toc` narrates the
outline (ambient — the buffer survives so the user can resume
typing). `FOCUS_CHANGED` now carries `kind` / `heading_level` /
`heading_title`; the default sound bank branches on
`transition == cell and kind == 'heading'` to voice "heading level
N: title" instead of the usual `{command}` readout.

**Remaining.** Parent-scope navigation (`{` / `}` jump to the
enclosing heading), fold / collapse (`z` toggles), and scope-
based selection (`select_heading_scope()` picks the focused
heading plus its children through the next same-level heading)
are still open. Those layer on top of the current flat
implementation without rewriting it.

**Forward-looking notes.**

- **Nested sections.** Real-world workflows want subsections
  (a top-level "data" section with "load" and "clean"
  subsections). The `heading_level` field already models nesting;
  what's missing is a `scope_range(cells, heading_index)` helper
  and focus-restoration rules when collapsing a parent of the
  current cursor.
- **Cross-cell dependencies.** Once F60 ships a persistent
  backend, sections become natural dependency boundaries (a
  "setup" section everyone re-runs, a "scratch" section nobody
  does). Worth recording but out of scope for a first pass.
- **Macro interaction.** F56's `expect`/`capture` steps will
  want a way to address "every cell in section X" — give
  sections stable ids, not just titles.
- **Persistence.** The current loader accepts missing `kind` as
  `COMMAND`; when folding / scope data lands, bump
  `Session.schema_version` so old loaders reject files they
  cannot render faithfully.

---

## F62 — Asynchronous execution queue

**Status: Shipped.**

**Sketch (shipped).** F60 introduced a persistent shell but the
submission path stayed synchronous: `app.execute(cell_id)` blocked
the keyboard read until the shell finished the command. That
worked while sessions were short and commands fast, but the
moment a cell takes seconds — a `pip install`, a `docker build`,
a long test run — the user could not type the next command, use
the action menu, or even hear a cancel cue. A "run every cell"
affordance (needed for F55 notebook runs) was literally
impossible: the driver would have to serialise the submissions
one kernel call at a time, losing the queue-up ergonomics a
notebook user expects.

The fix is an `ExecutionWorker` (`asat/execution_worker.py`): one
daemon thread, one `queue.Queue`, serial consumption. `Application`
now accepts `async_execution=True` at build time; when set, the
build spawns the worker and `Application.execute(cell_id)` calls
`worker.enqueue(cell_id)` instead of `kernel.execute(cell)`
directly. The worker publishes `COMMAND_QUEUED` with the depth
(so an audio cue confirms the keystroke landed the instant it
happens, even if three earlier commands are still running) and
`QUEUE_DRAINED` once the queue empties. The `EventBus` now holds a
`threading.RLock` so publishes from the worker thread and the
main thread cannot interleave; `RLock` (not `Lock`) because
handlers routinely re-enter `publish` (e.g. `PromptContext`
publishing `PROMPT_REFRESH` from a `COMMAND_COMPLETED` handler).
`Application.close()` stops the worker before tearing down the
runner so a cell in flight finishes against a live shell.

The CLI flips `async_execution=True` for every non-`--check`
invocation (`asat/__main__.py`); `--check` stays synchronous
because it never reads keys. Tests keep the synchronous default
so their deterministic ordering holds.

**Open follow-ups.**

- **Queue-aware cancel.** F1 will need to distinguish "cancel the
  running cell" (signal the shell) from "cancel the queued cell"
  (drop from the deque without running). Today `close(drain=False)`
  is the only off switch and it is binary.
- **Max-queue-depth policy.** A runaway batch submission could
  queue hundreds of cells. A soft cap with a narrated warning
  ("queue depth 50, wait for it to drain?") is the natural
  companion to F55's run-all affordance.
- **Drained-for-input narration.** On `QUEUE_DRAINED` today the
  bank plays a soft tick. F55 may want a richer "all caught up"
  spoken line so a user who submitted eight cells can walk away
  and come back on the cue.
- **Priority submissions.** No way today to slide a cell in front
  of the queue ("abort my long build, run this first"). Would
  require a priority queue and a policy for the cell currently
  held by the shell.
- **Multi-backend.** Once F50-F55 give each notebook its own
  backend, each notebook needs its own worker. A per-backend
  worker is the clean model; the shared `EventBus` lock already
  handles multi-producer safety.

---

## F63 — Event log as an append-only text file grouped by user interaction

**Gap.** F39 sketches an *interactive* event log viewer — an
in-process ring buffer the user can walk. What's missing is the
other half: a plain text file on disk that records every event,
grouped so a human (or `grep`) can see which user interaction
caused which downstream effect. Today a developer asking "what
ran when I pressed `]` in NOTEBOOK mode?" has to read
`asat/input_router.py`, follow the action handler to
`asat/notebook.py`, then trace the `FOCUS_CHANGED` subscribers
across `asat/terminal.py`, `asat/sound_engine.py`, and whatever
bindings the default bank picked — a tree reconstructed by hand
every time.

**Where it surfaces.** Any debugging that spans more than one
module; any new-contributor session trying to learn the
dispatch shape; any bug report of the form "I heard this cue at
the wrong moment" that today requires attaching a verbose
`--log` capture (F22) and reading chronological JSON.

**Sketch.** A new `EventLogFile` module subscribes wildcard on
the `EventBus` and writes to
`<workspace>/.asat/log/events-YYYY-MM-DD.log` (one file per local
day, rotated at midnight — re-uses F22's sink contract so
downstream tooling sees a single log stream). Formatting rule:
every `KEY_PRESSED` event opens a new group; every event
published between that keypress and the next keypress is
indented one level below it and annotated with its originating
module (via the event's `source` field, which every publisher
already sets). Example:

```
14:02:17.103 KEY_PRESSED name=']' modifiers=[] (input_router)
  14:02:17.104 ACTION_INVOKED action=next_heading matched=True level=None cell_id=c4 (input_router)
  14:02:17.104 FOCUS_CHANGED transition=cell new_cell_id=c4 kind=heading heading_level=2 heading_title='Setup' (notebook)
  14:02:17.106 AUDIO_SPOKEN binding_id=focus_changed_heading text='heading level 2 Setup' voice_id=narrator (sound_engine)
```

Auto-flush on every write so a crash loses at most the current
line; tail-safe (no rewrite of earlier bytes). A `:log open`
meta-command prints the current log file path and narrates the
last N lines; a `:log tail` sub-command streams new lines into
the speech console (F28) so a second session can listen in.
Honour F41's silent-sink guard — if the workspace is read-only,
degrade to `stderr` with a one-time `SESSION_WARNING` event
rather than crashing.

**Why this complements F39 not duplicates it.** F39 is an
in-memory, narratable, *editable* view ("heard a cue, want to
change the binding"). F63 is a file on disk, never narrated by
default, optimised for *post-hoc* reading with `tail -f` /
`grep` / `less` — two different consumer shapes that happen to
share the same event-subscription pattern.

**Depends on / pairs with.** F22 (diagnostic log file) —
F63 picks up the same rotation and path conventions. F50
(workspace directory) — the log lives in `<workspace>/.asat/log/`
so per-project traces stay scoped to the project. F39 — the
file format is a superset of F39's bounded-ring entries, so a
future `:log open --viewer` could load a saved file back into
the interactive viewer.

---

## F64 — Keybinding introspection: `:bindings` meta-command and generated reference

**Status: Shipped.** `BindingEntry`,
`format_key`, `binding_report`, and
`format_bindings_markdown` live in `asat/input_router.py`;
`:bindings` (ambient meta-command) publishes a
HELP_REQUESTED listing every binding grouped by mode and
honours optional `mode` and `key` filters
(`:bindings notebook up`).
[`docs/BINDINGS.md`](BINDINGS.md) is generated by
`python -m asat.tools.dump_bindings --write`, and
`tests/test_bindings_introspection.py::BindingsDocInSyncTests`
fails CI when it drifts. Deferred: depth-two chain
introspection (`--verbose` flag), and re-reading the
*effective* binding from a user override file (waits on F25).

**Gap.** `asat/input_router.py` is the source of truth for
every keystroke the app responds to, but there is no surface
— runtime or on-disk — that answers "given mode X and
key combination Y, which action runs?" without reading code.
The cheat sheet (`HELP_LINES`) lists a curated subset; the
`USER_MANUAL.md` tables are hand-maintained and drift (see the
test gate `test_every_meta_command_is_documented` that caught
`:heading` / `:toc` missing last week).

**Where it surfaces.** New contributors cannot see the
dispatch shape. Power users who wire up F25 (remappable keys)
cannot confirm their override actually took effect. Tests
cannot assert "mode X binds key Y to action Z" without
reaching into private router state. The F63 event-log reader
needs a way to jump from "this `ACTION_INVOKED` fired" to
"here is the `(mode, key, modifiers) → action` binding that
caused it".

**Sketch.** Two surfaces, one data source.

1. **Runtime `:bindings` meta-command** (INPUT mode, ambient —
   buffer survives). Accepts optional `mode` and `key`
   filters. Output format, one per line:
   ```
   NOTEBOOK  ']'           →  next_heading()                 [input_router]
   NOTEBOOK  '['           →  prev_heading()                 [input_router]
   NOTEBOOK  '1'..'6'      →  next_heading(level=N)          [input_router]
   INPUT     Enter         →  submit() -> Application.execute()  [input_router, app]
   INPUT     Ctrl+U        →  clear_input_buffer()           [input_router]
   ```
   The trailing `[module]` annotations come from a
   static-import walk of the action handler — one line per
   high-level function the binding resolves to, in call
   order. Published through the speech console (F28) so blind
   users hear it; echoed to the terminal renderer so sighted
   developers can copy-paste.
2. **Generated `docs/BINDINGS.md`**. A small script
   (`python -m asat.tools.dump_bindings`) walks the router at
   import time and emits the same table as Markdown, grouped
   by mode with anchor links. `test_bindings_doc_in_sync`
   re-runs the dump in-memory and diffs against the committed
   file, so a new binding that forgets to regenerate the doc
   fails CI the same way `test_every_meta_command_is_documented`
   catches missing meta-command rows.

**Data source.** One public helper on `InputRouter`:
`binding_report() -> tuple[BindingEntry, ...]` where
`BindingEntry = (mode: FocusMode, key_spec: str,
modifiers: frozenset[Modifier], action: str, chain:
tuple[str, ...])`. `chain` is the ordered sequence of
high-level functions the action resolves to — computed by
inspecting the ActionHandler's registered Python callable and
following `publish_event` / cursor-method calls one step deep.
Both the `:bindings` command and the doc generator read from
this helper; they cannot drift.

**Why one function per line is the whole point.** The user
asked for "high-level functions one per line that are run in
response". A multi-line dispatch tree per binding is harder to
scan than a flat list where each line is independently
`grep`-able. Reserve depth-two chains for a `:bindings
<key> --verbose` flag; the default stays flat.

**Depends on / pairs with.** F25 (remappable keybindings) —
if F25 lands first, the table reads the *effective* binding
(default overlaid with user file) so users can confirm their
overrides. F63 (event log file) — each `ACTION_INVOKED` line
in the log gets a pointer to the binding entry that fired it,
closing the "I just saw this event — where is the keystroke
that caused it?" loop.

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
