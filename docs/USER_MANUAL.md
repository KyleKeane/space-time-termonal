# ASAT user manual

The Accessible Spatial Audio Terminal (ASAT) is a terminal built from
the ground up for blind developers on Windows. This manual gets you
productive with the minimum number of keystrokes and no need to look
at the screen. Everything you need to hear is spoken; everything you
need to trigger is a key.

> ASAT is self-voicing. You do not need NVDA, JAWS, or Narrator to
> read its interface — the spatial-audio framework narrates every
> state change directly. You can still run a screen reader on top for
> other applications; ASAT will not fight it.

---

## Launching a session

```
python -m asat                      # interactive session, text trace on stdout
python -m asat --live               # play audio on the speaker (Windows today)
python -m asat --wav-dir /tmp/asat  # also write every rendered buffer to WAV
python -m asat --quiet              # suppress the text trace, audio only
python -m asat --bank mybank.json   # start from a saved SoundBank
python -m asat --session s.json     # resume an existing session; saved on exit
python -m asat --log events.jsonl   # write one JSON line per event to disk
python -m asat --check              # build, print a diagnostic summary, exit
python -m asat --version            # print the version string and exit
```

Every flag is optional and they compose. The common launch recipes:

- **On Windows:** `python -m asat --live`. You hear the session-start
  chime and every subsequent narration on the speaker. The text trace
  on stdout is secondary; most users will want `--quiet` once they
  are comfortable driving by ear alone.
- **On macOS/Linux today:** `python -m asat --wav-dir /tmp/asat`.
  Each rendered buffer is written to a numbered WAV under
  `/tmp/asat`. Live playback is being worked on
  (FEATURE_REQUESTS.md F6); until then, WAV capture plus a
  screen-reader-friendly player is the recommended path.
- **Resume or start a session:** `python -m asat --session work.json`.
  If the file exists the session loads and you hear a "loaded"
  narration; if it does not exist a fresh session starts and the file
  is created on exit. Either way the same path is rewritten on exit
  with whatever cells you ran. `:save` (INPUT mode) persists mid-run.
- **Record a diagnostic log:** `python -m asat --log /tmp/events.jsonl`.
  Every bus event lands as a single JSON line (`event_type`, `payload`,
  `source`, `timestamp`), starting with `session.created`. The file is
  truncated on each launch so logs never grow unboundedly. Useful for
  post-mortems, bug reports, or feeding events to an external replayer.
- **Sanity-check your install:** `python -m asat --check`. Builds the
  Application, prints the picked sink, bank, session, and TTY state,
  then runs the four-step diagnostic self-test (bank validates, every
  voice speaks, one cue lands per covered event, live playback
  reachable) and exits with code 0 when every step passes. See
  [Diagnosing audio issues](#diagnosing-audio-issues) for what each
  step verifies.

If you run `python -m asat` with no `--live` / `--wav-dir` flag the
CLI prints a one-line stderr hint: `[asat] audio is going to the in-
memory sink. Pass --live (Windows) or --wav-dir DIR to hear or
capture it.` That is not an error; audio is still being rendered and
processed, it just isn't going anywhere audible.

**If stdin is not a TTY** (you piped input into ASAT, or you're
inside a sandbox without a real console) the CLI exits cleanly with
`[asat] cannot start: ASAT needs an interactive terminal (a TTY). …`
and returns exit code 2, rather than a raw `termios.error`
traceback. Launch from a real terminal to fix it.

### Environment variables

| Variable   | Effect                                                         |
|------------|----------------------------------------------------------------|
| `ASAT_HOME`| Directory ASAT stores per-user state in. Defaults to `~/.asat`.|

Setting `ASAT_HOME=/some/dir` redirects ASAT's per-user state —
today just the first-run-onboarding sentinel — to that directory.
Useful for portable installs, for running two ASAT copies side by
side, and for CI jobs that should not write into the runner's home.
The variable is read at launch time; unset it to return to the
default.

### What you should hear and see on launch

The moment the binary starts:

1. **Audio.** A short rising chime (the SESSION_CREATED binding in
   the default SoundBank) plays from directly overhead, followed by
   the `focus_shift` cue as the cursor drops into INPUT mode on the
   first empty cell. If you hear neither, your sink is silent —
   re-launch with `--live` (Windows) or `--wav-dir DIR` (elsewhere)
   and check the `[asat]` line that `--wav-dir` produces.
2. **Text trace (unless `--quiet`).** One line reading
   `[asat] session <id> ready. Type :help for the keystroke cheat
   sheet, :quit to exit.`, then `[input #<short-id>]` to confirm you
   are in INPUT mode.

If neither the chime nor the banner appear, the session did not
start cleanly — see the troubleshooting table at the end of this
file.

### First-run onboarding

The **very first** time ASAT starts on a given machine you will
also hear a short spoken welcome ("Welcome to ASAT. Type colon
help to hear the keystroke cheat sheet.") and the text trace prints
a four-line tour explaining the key meta-commands (`:help`,
`:commands`, Escape, `:quit`). The tour plays from the narrator
voice, on the left, and is spelled letter-by-letter
(`"h, e, l, p"`) so the TTS pronounces each character.

A sentinel file at `~/.asat/first-run-done` records that you've
seen the tour; subsequent launches skip it. `--quiet` and
`--check` also skip the tour. Delete the sentinel file and re-launch
if you ever want to hear the welcome again.

---

## The five-minute tour

1. **Launch** ASAT with the recipe above for your platform
   (`--live` on Windows, `--wav-dir DIR` on POSIX). You'll hear a
   short rising chime (the session start) from directly overhead —
   that means the session is live.
2. **Type `:help`** then Enter. You'll hear a short spoken summary
   of the keys, and the text trace prints the full cheat sheet.
   You can do this any time you get lost.
3. **Type a command** — e.g. `dir`, `python --version`, `git status`.
   You are automatically in INPUT mode, so keys go into the current
   cell.
4. **Press Enter**. You'll hear a triangle-wave "submit" cue on the
   left, then the narrator reads output lines as they stream, then a
   major-chord "success" chime on Enter's exit code, or a low minor
   "failure" chord on the right if the command failed. ASAT
   auto-advances to a fresh empty cell in INPUT mode, so you can
   immediately type your next command without pressing anything else.
5. **Press Escape**. You're now in NOTEBOOK mode. Up / Down walks
   between cells. (You only need this when you want to navigate
   history; running a new command just means typing and Enter.)
6. **Press Ctrl+O**. You're now in OUTPUT mode, stepping line-by-line
   through the selected cell's captured output. Escape leaves.
7. **Press Ctrl+,** (or type `:settings` then Enter in INPUT mode).
   You're in the settings editor. Escape or Ctrl+Q leaves.

That's the whole shape. The rest of this manual is the detail behind
each step.

---

## What a cell *is* (and is not) today

ASAT is a notebook in the sense that you have an ordered list of
cells per session, each cell is editable, each remembers its own
captured stdout/stderr and exit code, and the whole list saves to
JSON with `--session file.json`. You can navigate, reorder,
duplicate, and delete cells without leaving the keyboard.

It is **not** Jupyter. Two limits to be aware of from day one:

1. **All cells share one shell.** On POSIX hosts ASAT launches a
   single long-lived bash at session start and routes every cell's
   command through it. So `cd /tmp` in cell 1 *does* change the cwd
   of cell 2, `export FOO=bar` in cell 1 is visible in cell 2, and
   shell options (`set -o pipefail`, `shopt -s nullglob`) carry
   forward — exactly as they would at a real prompt. If you would
   rather isolate each cell in its own one-shot subprocess, launch
   with `--no-shared-shell`. A long-lived shell amplifies blast
   radius (a stuck `read`, a `set -e` exit) — when bash is missing
   or you opt out, ASAT silently falls back to per-cell subprocesses
   and prints a one-line stderr note. Windows currently always uses
   per-cell subprocesses; a Windows persistent backend is tracked
   alongside [F60](FEATURE_REQUESTS.md#f60--persistent-computational-backend-shared-shell--repl).
2. **Cells are not interactive terminals (no PTY).** A cell is a
   command string plus its captured output and exit code. Programs
   that take over the screen — `vim`, `less`, `top`, `python`
   without `-c`, anything curses-based — will not work inside a
   cell. Use a one-shot invocation (`python -c '...'`,
   `git --no-pager log`) instead.
3. **Cells are a flat ordered list.** No sections, no folding, no
   parent/child grouping. Up / Down walks every cell linearly.
   Sections and folds are tracked as
   [F61](FEATURE_REQUESTS.md#f61--cell-hierarchy-sections-folds-and-grouping).
4. **Submissions queue; they don't run in parallel.** Submitting a
   cell while an earlier cell is still running is fine — the new
   submission lands in a serial queue, you hear a soft tick
   confirming the keystroke landed, and it runs the moment the
   earlier cells finish. Keyboard navigation, the action menu, and
   meta-commands stay responsive while long commands are still in
   flight. The queue drains in submission order; there's no way to
   push a cell to the front or cancel a queued-but-not-started
   cell yet (tracked as
   [F62](FEATURE_REQUESTS.md#f62--asynchronous-execution-queue)).

Within those limits the notebook surface is real: edit a previous
cell, re-run it, walk its captured output line by line in OUTPUT
mode, save the session, resume it tomorrow with every cell intact.

---

## Focus modes

You are always in exactly one of five modes. The current mode
decides what keys do.

| Mode       | What you're doing                                  |
|------------|----------------------------------------------------|
| `NOTEBOOK` | Walking between cells (no keys go into a buffer). |
| `INPUT`    | Typing a command into the active cell.             |
| `TEXT_INPUT` | Composing a new prose cell in-place (F27). Press `i` in NOTEBOOK to enter; Enter creates, Escape abandons. |
| `OUTPUT`   | Stepping line-by-line through captured output.     |
| `SETTINGS` | Driving the live SoundBank editor.                 |

Every mode transition announces itself: you'll hear the
`focus_shift` cue overhead and the system voice names the new mode
(`"input"`, `"notebook"`, `"output"`, `"settings"`, `"text_input"`).
Walking between cells within NOTEBOOK mode plays `nav_blip` instead
and the narrator reads the focused cell's command. Typing into any
buffer (INPUT or TEXT_INPUT) is silent on purpose — the
`insert_character` action echoes the literal character instead. If
you lose track of where you are, just press Escape — it always takes
you one level out (INPUT / OUTPUT / TEXT_INPUT → NOTEBOOK, SETTINGS
→ close), and the narrator confirms the new mode.

---

## NOTEBOOK mode — walking cells

| Key        | What it does                                      |
|------------|---------------------------------------------------|
| Up / Down  | Previous / next cell.                             |
| Home / End | First / last cell.                                |
| Enter      | Enter INPUT mode on the focused cell.             |
| Ctrl+N     | Create a new empty cell below.                    |
| Ctrl+O     | Enter OUTPUT mode on the focused cell's output.   |
| Ctrl+R     | Repeat the most recent narration.                 |
| Ctrl+,     | Open the settings editor.                         |
| `d`        | Delete the focused cell.                          |
| `y`        | Duplicate the focused cell (inserts a copy below).|
| `i`        | Begin composing an in-place text cell; lands in TEXT_INPUT mode (F27). |
| Alt+Up / Alt+Down | Move the focused cell up / down within the session. |

Moving between cells plays the `nav_blip` cue and the narrator reads
the cell's command so you know what you're standing on. Empty cells
narrate as silence; use Enter to drop into INPUT mode and start
typing.

---

## INPUT mode — typing commands

You're in INPUT mode the moment you start typing in a fresh cell,
or when you press Enter from NOTEBOOK mode.

| Key       | What it does                                      |
|-----------|---------------------------------------------------|
| Any char  | Inserted at the caret.                            |
| Backspace | Deletes the character before the caret.           |
| Delete    | Deletes the character under the caret.            |
| Left / Right | Move the caret one character.                  |
| Home / End | Jump the caret to the start / end of the line.   |
| Up / Down | Walk command history (F4): Up recalls the previous command, Down steps forward; Down past the most recent restores the in-progress draft. |
| Ctrl+A / Ctrl+E | Alias for Home / End (readline-style).       |
| Ctrl+W    | Delete the word immediately before the caret.     |
| Ctrl+U    | Delete from the start of the line up to the caret. |
| Ctrl+K    | Delete from the caret to the end of the line.    |
| Ctrl+R    | Repeat the most recent narration.                 |
| Enter     | Submits the command to the execution kernel.      |
| Escape    | Commits the buffer into the cell and returns to NOTEBOOK (does not run). |

---

## TEXT_INPUT mode — composing a prose cell in-place

Press `i` from NOTEBOOK to begin a text cell without leaving the
flow. The cursor enters TEXT_INPUT mode with an empty buffer; every
printable key goes into the buffer (no meta-commands, no history
recall). Enter splices the buffer into the notebook as a new text
cell *immediately after the currently focused "anchor" cell* and
focuses it in NOTEBOOK mode. Escape (or an empty / whitespace-only
buffer on Enter) abandons without creating anything.

| Key       | What it does                                      |
|-----------|---------------------------------------------------|
| Any char  | Inserted at the caret.                            |
| Backspace | Deletes the character before the caret.           |
| Delete    | Deletes the character under the caret.            |
| Left / Right | Move the caret one character.                  |
| Home / End | Jump the caret to the start / end of the buffer. |
| Ctrl+A / Ctrl+E | Alias for Home / End.                       |
| Ctrl+W    | Delete the word immediately before the caret.     |
| Ctrl+U    | Delete from the start of the buffer up to the caret. |
| Ctrl+K    | Delete from the caret to the end of the buffer.   |
| Enter     | Create the text cell and return to NOTEBOOK.      |
| Escape    | Abandon the draft (no cell is created).           |

The `:text <prose>` meta-command in INPUT mode is still available
for scripted / dictated insertion; `i` is the hands-on counterpart
where you want the buffer to be the prose body directly.

---

### Meta-commands

Any line starting with `:` is a **meta-command**. These are
intercepted before the kernel ever sees them — the buffer is
discarded and the cell is not modified.

| Meta-command | Effect                                               |
|--------------|------------------------------------------------------|
| `:help`      | Narrate + print the keystroke cheat sheet.           |
| `:help topics` | List every focused `:help <topic>` micro-tour.     |
| `:help <topic>` | Narrate one tour: `navigation`, `cells`, `settings`, `audio`, `search`, `meta`. |
| `:welcome`   | Replay the first-run welcome tour (F44).             |
| `:repeat`    | Re-hear the most recent narration (same as Ctrl+R).  |
| `:settings`  | Open the settings editor (same as Ctrl+,).           |
| `:save`      | Save the current session to `--session` path (no-op without one). |
| `:quit`      | Exit ASAT.                                           |
| `:delete`    | Delete the focused cell (same as `d` in NOTEBOOK).   |
| `:duplicate` | Duplicate the focused cell (same as `y` in NOTEBOOK).|
| `:pwd`       | Announce the current working directory.              |
| `:state`     | Narrate focus mode, cell position, session id, and cwd. |
| `:commands`  | List every available meta-command.                   |
| `:reset bank` | Reset the whole sound bank to the built-in defaults (also `:reset all`). |
| `:heading <level> <title>` | Append a heading landmark at level 1..6 for NVDA-style navigation (F61). |
| `:text <prose>` | Append a non-executable prose cell carrying narrative alongside commands and headings (F27). |
| `:toc`       | Narrate the notebook's heading outline.              |
| `:workspace` | Re-announce the active project root and notebook count (no-op without a workspace). |
| `:list-notebooks` | Narrate every notebook in the workspace by name. |
| `:new-notebook <name>` | Create a fresh notebook on disk; restart ASAT with `asat <root> <name>` to open it. |
| `:bindings`  | List every active keybinding grouped by mode (F64). Add a `<mode>` or `<key>` argument to filter (`:bindings notebook`, `:bindings up`, or both). |
| `:bookmark <name>` | Capture the focused cell under `<name>` for later recall (F35). Names are single tokens; reusing a name rebinds it. |
| `:unbookmark <name>` | Remove a previously registered bookmark (F35). |
| `:bookmarks` | Narrate every bookmark and the cell index it points at (F35). |
| `:jump <name>` | Move focus to the cell registered under `<name>` (F35). Leaves you in NOTEBOOK mode at the target cell. |
| `:verbosity <level>` | Set the bank-wide narration ceiling to `minimal`, `normal`, or `verbose` (F31). Chattier tiers are silenced when the level drops. Plain `:verbosity` narrates the allowed values and the current setting. |
| `:reload-bank` | Discard any in-memory bank edits and re-read the on-disk bank from the configured `bank_path` (F3). Refuses while the settings editor is open; emits a hint when no bank path is configured or the file fails to parse. |
| `:tts list` | Describe every TTS engine registered in the pluggable registry (`docs/AUDIO.md`) and mark which ones are installed on this host. |
| `:tts use <id>` | Hot-swap the live TTS engine (`pyttsx3`, `espeak-ng`, `say`, `tone`). The next narration is rendered through the new backend without restarting ASAT. |
| `:tts set <param> <value>` | Tune the current engine — e.g. `:tts set rate 180` or `:tts set voice en-us`. Parameter names are engine-specific; `:tts list` enumerates them. |

Meta-command names are **case-insensitive** — `:HELP`, `:Help`, and
`:help` all do the same thing. A single trailing argument is
allowed (`:help settings`) and is surfaced on the submit event for
observers that care.

If you mistype (e.g. `:setings`), the router keeps the line out of
the shell, clears the buffer, and narrates a hint like *"unknown
meta-command `:setings` — did you mean `:settings`?"*. Type
`:commands` any time to hear the full list.

### Running a command

On Enter:

1. The `submit` cue plays (left-hand triangle wave).
2. The narrator says "command submitted".
3. Output streams in: the narrator reads each stdout line from the
   left, the alert voice reads each stderr line from the right.
4. When the process exits you hear either the major-chord success
   chime (exit code 0) or the low minor-second failure chord plus
   the alert voice reading the exit code (nonzero).
5. On failure, the alert voice also auto-reads the last few stderr
   lines so you hear *what* went wrong without having to jump into
   OUTPUT mode. The tail defaults to three lines (configurable in
   `StderrTailAnnouncer`); disable the `command_failed_stderr_tail`
   binding in the settings editor if you prefer the minimal cue.

Press **Ctrl+C** in INPUT mode to cancel the cell that is currently
running. The keystroke needs the F62 async-execution worker (the CLI
turns it on by default), because a synchronous run would freeze the
keyboard read while the command is in flight. Cancellation publishes
`COMMAND_CANCELLED` instead of `COMMAND_FAILED` so the audio bank
plays the dedicated `cancel` cue and any partial output the cell
already collected is preserved on the cell record. With nothing
running, Ctrl+C surfaces a `HELP_REQUESTED` hint ("No command is
currently running.") so the keystroke is never silently dropped.

### Prompt context on re-entry

Every time you step back into INPUT mode on a cell *after at least
one command has finished*, ASAT emits a `PROMPT_REFRESH` event that
carries the trailing exit code, the finishing cell id, whether the
last run timed out, and the current working directory. The visible
terminal prints a compact line like `[prompt exit=0 cwd=/work]`;
the system voice stays quiet after a clean run but narrates
`"last exit 1"` (or whatever the nonzero code was) when the last
command failed. A pristine session emits nothing until the first
command finishes, so you won't hear noise on launch.

---

## OUTPUT mode — re-reading output

Press Ctrl+O from NOTEBOOK mode (with a cell focused) to step through
the cell's captured output one line at a time. Useful when a
command's narration flew by and you want to re-hear line 47.

| Key         | What it does                                      |
|-------------|---------------------------------------------------|
| Up / Down   | Previous / next line.                             |
| PageUp / PageDown | Jump a page.                                |
| Home / End  | Jump to the first / last captured line.           |
| `/`         | Search: type a query (case-insensitive). The cursor jumps to the first match as you type. Enter commits, Escape restores your starting line. |
| `n` / `N`   | After a committed search, cycle to the next / previous match. |
| `g`         | Jump to line: type a 1-based line number, Enter commits. |
| `p` / Space | Play/pause: auto-advance through the buffer at ~1.5 s per line. Any other key pauses; Escape leaves OUTPUT mode. |
| F2 / Ctrl+. | Open the actions menu.                            |
| Escape      | Return to NOTEBOOK mode.                          |

Each step plays the `focus_shift` cue and the narrator reads the
focused line. Stderr lines come from the alert voice on the right;
stdout lines come from the narrator on the left — the spatial split
tells you which stream a line came from without the narrator having
to say "stderr:" every time.

---

## Actions menu — context-sensitive affordances

The actions menu is the keyboard-driven equivalent of a right-click
menu. Press **F2** (or **Ctrl+.** as a fallback on keyboards without
an F-row) from NOTEBOOK, INPUT, or OUTPUT mode and a short list of
items appears based on where you're focused:

* **NOTEBOOK** — edit the focused command, or explore its output.
* **INPUT** — submit the command, or cancel editing.
* **OUTPUT** — copy the focused line, copy the whole buffer, copy
  just stderr, or return to the notebook.

While the menu is open the keymap is modal:

| Key       | What it does                                      |
|-----------|---------------------------------------------------|
| Up / Down | Previous / next item (clamped at the ends).       |
| Enter     | Invoke the focused item; the menu closes.         |
| Escape    | Close the menu without invoking anything.         |

Every transition narrates. Opening the menu reads the item labels,
Up/Down reads the newly-focused label, Enter announces the invocation
and fires the `menu_activate` cue, and Escape fires `menu_close`.
Copy items route through the clipboard. `python -m asat` wires up
`SystemClipboard`, which tries the native tool for your platform
(Wayland's `wl-copy`, then `xclip`, then `xsel` on Linux; `pbcopy`
on macOS; `clip` on Windows) and falls back to in-process storage
with a spoken warning if none of them is installed. Tests and
embeddings that build the Application directly still get the
deterministic `MemoryClipboard` by default.

---

## SETTINGS mode — reshaping audio without restarting

The settings editor is the live control surface for every voice,
sound, and binding in the terminal. You can enter it any time.

**Open:** Ctrl+, from NOTEBOOK mode, or type `:settings` from
INPUT mode.

**Close:** Ctrl+Q, or press Escape at the top level.

The editor walks a three-level tree:

```
SECTION  ["voices", "sounds", "bindings"]
   ↓
RECORD   each Voice, SoundRecipe, or EventBinding in the section
   ↓
FIELD    the typed fields on that record
```

| Key                 | What it does                                    |
|---------------------|-------------------------------------------------|
| Up / Down           | Previous / next item at the current level.      |
| Right / Enter       | Descend (section → record → field).             |
| Left / Escape       | Ascend (or close at top level).                 |
| `e`                 | Begin editing the focused field.                |
| `/`                 | Open the cross-section search overlay.          |
| `n` / `N`           | Next / previous search match after commit.      |
| Ctrl+Z              | Undo the most recent field edit.                |
| Ctrl+Y              | Redo the most recently undone edit.             |
| Ctrl+S              | Save the bank to disk.                          |
| Ctrl+Q              | Close the editor.                               |

Undo / redo stacks hold up to 64 edits and survive saves, so you
can revert past a save if you realise the regression landed
earlier. Pressing Ctrl+Z while composing a replacement (inside the
edit sub-mode) is ignored so it can't silently discard your
in-progress text. The cursor jumps to the field each history step
mutated so the narrator re-reads the value — useful when you want
to hear "what changed" in the moment.

### Editing a field

When you press `e` on a focused field, you enter the **edit sub-
mode**. The narrator reads the current value; printable characters
compose the replacement.

| Key       | What it does                                          |
|-----------|-------------------------------------------------------|
| Any char  | Appended to the edit buffer.                          |
| Backspace | Removes the last character of the buffer.             |
| Enter     | Commits the buffer as the new value.                  |
| Escape    | Cancels the edit; the field keeps its old value.      |

The parser enforces the field's declared type:

* `rate`, `pitch`, `volume`, `azimuth`, `elevation` — floats.
* `priority` — integer.
* `enabled` — `true` / `false` (or `yes` / `no`, `1` / `0`, `on` /
  `off`).
* `voice_id`, `sound_id` — text; type `null` (or leave empty) to
  clear.
* `params` — a JSON object.
* `voice_overrides`, `sound_overrides` — JSON objects; keys are
  restricted to the numeric fields on `Voice` / `SoundRecipe`.
* Everything else (ids, templates, predicates) — raw text.

A rejected edit keeps your old value and the narrator reads the
error. A successful edit plays the `settings_chime` and immediately
takes effect — you don't need to restart. Ctrl+S writes the whole
bank to disk so the change survives a restart.

### Searching the bank

Press `/` at any level to open a cross-section search overlay. The
overlay is a sub-mode modelled on the output-cursor search (F16):
while it is active, printable characters extend the query buffer,
Backspace trims it, Enter commits, and Escape restores the cursor
to exactly where you were before.

The query runs a case-insensitive substring match across every
section at once:

* **Voices** and **Sounds** match on their `id`.
* **Bindings** match on `id`, `event_type`, `voice_id`, or
  `sound_id` — enough to find "every binding that uses the narrator
  voice" or "every routing for `command.completed`".

The first hit is announced and the cursor parks at **record** level
so you can descend with Right/Enter to the fields if you want to
edit. After committing, `n` and `N` cycle forward and backward
through the remaining matches (wrapping at the ends). A zero-match
query still narrates the count so you know the overlay heard you.

### Resetting to defaults

If a field, record, or whole section has drifted from the stock
bank and you want to start over, press **Ctrl+R** to open the
reset confirmation. The scope follows your cursor:

* At **field** level, only the focused field is restored.
* At **record** level, the whole record goes back to its default.
* At **section** level, every record in the section is replaced.

Press **Enter** to confirm the reset or **Escape** to cancel — no
other key is accepted while the confirmation is open, so a stray
motion key can't accidentally wipe a record. Every reset is a
single undoable step, so Ctrl+Z afterwards puts your edits back.

From **INPUT** mode you can also type `:reset bank` (or the alias
`:reset all`) to reset the entire bank at once; this opens the
settings editor and walks straight into the confirmation. Finer-
grained scopes (`:reset section`, `:reset record`, `:reset field`)
are deliberately not accepted from INPUT — use Ctrl+R inside the
editor where the cursor gives the reset a specific target.

If the targeted slice is already at defaults, the confirmation
still opens but pressing Enter narrates "already at defaults" and
leaves the bank untouched (nothing to undo).

### What the fields mean

For a full reference of every field and how the audio pipeline
consumes them, see [AUDIO.md](AUDIO.md). The short version:

* **Voices** are TTS narrators. Tune `rate` / `pitch` / `volume` to
  taste; move a voice in space with `azimuth` (-180..180) and
  `elevation` (-90..90).
* **Sounds** are non-speech cues. Tone recipes take a `frequency`
  and `duration` in `params`; chord recipes take a list of
  `frequencies`.
* **Bindings** are the routing table. A binding picks an event
  (`event_type`), optionally a voice and a sound, and a narration
  template. Use `voice_overrides` / `sound_overrides` to reshape
  the voice or sound for just this one binding without cloning the
  whole record.

---

## The keystroke cheat sheet

Every key you need, one table.

| Mode       | Key               | Action                                |
|------------|-------------------|---------------------------------------|
| NOTEBOOK   | Up / Down         | Prev / next cell                      |
| NOTEBOOK   | Home / End        | First / last cell                     |
| NOTEBOOK   | Enter             | Enter INPUT mode                      |
| NOTEBOOK   | Ctrl+N            | New empty cell                        |
| NOTEBOOK   | Ctrl+O            | Enter OUTPUT mode                     |
| NOTEBOOK   | Ctrl+,            | Open settings editor                  |
| NOTEBOOK   | `d`               | Delete focused cell                   |
| NOTEBOOK   | `y`               | Duplicate focused cell                |
| NOTEBOOK   | Alt+Up / Alt+Down | Move focused cell up / down           |
| NOTEBOOK   | Ctrl+R            | Repeat the most recent narration      |
| NOTEBOOK   | F2 / Ctrl+.       | Open actions menu                     |
| NOTEBOOK   | `]` / `[`         | Next / previous heading (any level)   |
| NOTEBOOK   | `1`..`6`          | Next heading of that level (F61)      |
| NOTEBOOK   | `}` / `{`         | Next / prev parent-scope heading (F27)|
| NOTEBOOK   | `i`               | Begin an in-place text cell (F27); lands in TEXT mode |
| TEXT       | Enter             | Create a text cell from the buffer after the anchor (F27) |
| TEXT       | Escape            | Abandon the draft without creating a cell (F27) |
| TEXT       | Backspace / Delete | Delete char before / at caret        |
| TEXT       | Left / Right      | Move caret one character              |
| TEXT       | Home / End        | Caret to start / end (also Ctrl+A / Ctrl+E) |
| TEXT       | Ctrl+W / Ctrl+U / Ctrl+K | Kill word / to-start / to-end  |
| TEXT       | F2 / Ctrl+.       | Open actions menu                     |
| INPUT      | Enter             | Submit command                        |
| INPUT      | Backspace         | Delete char before caret              |
| INPUT      | Delete            | Delete char under caret               |
| INPUT      | Left / Right      | Move caret one character              |
| INPUT      | Home / End        | Caret to start / end                  |
| INPUT      | Up / Down         | Walk command history (F4)             |
| INPUT      | Ctrl+A / Ctrl+E   | Caret to start / end (readline)       |
| INPUT      | Ctrl+W            | Delete word before caret              |
| INPUT      | Ctrl+U            | Delete from start of line to caret    |
| INPUT      | Ctrl+K            | Delete from caret to end of line      |
| INPUT      | Ctrl+C            | Cancel the running command (F1)       |
| INPUT      | Escape            | Leave INPUT without running           |
| INPUT      | Ctrl+R            | Repeat the most recent narration      |
| INPUT      | F2 / Ctrl+.       | Open actions menu                     |
| INPUT      | `:help`⏎          | Narrate + print the cheat sheet       |
| INPUT      | `:welcome`⏎       | Replay the first-run welcome tour     |
| INPUT      | `:repeat`⏎        | Re-hear the most recent narration     |
| INPUT      | `:settings`⏎      | Open settings editor                  |
| INPUT      | `:save`⏎          | Save session                          |
| INPUT      | `:quit`⏎          | Exit ASAT                             |
| INPUT      | `:delete`⏎        | Delete focused cell                   |
| INPUT      | `:duplicate`⏎     | Duplicate focused cell                |
| INPUT      | `:pwd`⏎           | Announce working directory            |
| INPUT      | `:state`⏎         | Announce focus, cell position, cwd    |
| INPUT      | `:commands`⏎      | List every meta-command               |
| INPUT      | `:reset bank`⏎    | Reset the whole bank to defaults      |
| INPUT      | `:reload-bank`⏎   | Reload the bank from disk (F3)        |
| INPUT      | `:heading <N> <title>`⏎ | Append a level-N heading landmark |
| INPUT      | `:text <prose>`⏎  | Append a prose/text cell (F27)        |
| INPUT      | `:toc`⏎           | Narrate the heading outline           |
| OUTPUT     | Up / Down         | Prev / next line                      |
| OUTPUT     | PageUp / PageDown | Jump a page                           |
| OUTPUT     | Home / End        | First / last line                     |
| OUTPUT     | `/`               | Search (live), Enter commits, Escape restores |
| OUTPUT     | `n` / `N`         | Next / previous search match          |
| OUTPUT     | `g<number>` Enter | Jump to 1-based line number           |
| OUTPUT     | `p` / Space       | Play / pause auto-advance (F24)       |
| OUTPUT     | Escape            | Back to NOTEBOOK                      |
| OUTPUT     | F2 / Ctrl+.       | Open actions menu                     |
| MENU       | Up / Down         | Prev / next item                      |
| MENU       | Enter             | Invoke focused item                   |
| MENU       | Escape            | Close without invoking                |
| SETTINGS   | Up / Down         | Prev / next item                      |
| SETTINGS   | Right / Enter     | Descend                               |
| SETTINGS   | Left / Escape     | Ascend / close                        |
| SETTINGS   | `e`               | Begin edit (at field level)           |
| SETTINGS   | `/`               | Search (live), Enter commits, Escape restores |
| SETTINGS   | `n` / `N`         | Next / previous search match          |
| SETTINGS   | Ctrl+Z / Ctrl+Y   | Undo / redo field edit                |
| SETTINGS   | Ctrl+R            | Reset to defaults at cursor scope (Enter confirms, Escape cancels) |
| SETTINGS   | Ctrl+S            | Save bank                             |
| SETTINGS   | Ctrl+Q            | Close editor                          |

---

## The sound lexicon

The default bank ships with these cues. If you change them in the
editor, this table is your starting map.

| Cue               | When you hear it                              | Where it comes from       |
|-------------------|-----------------------------------------------|---------------------------|
| `session_chime`   | A new session starts                          | Overhead                  |
| `submit`          | You pressed Enter in INPUT mode               | Left                      |
| `start`           | The kernel has begun running the command      | Left                      |
| `success_chord`   | Command exited with code 0                    | Left                      |
| `failure_chord`   | Command exited non-zero                       | Right                     |
| `cancel`          | A command was cancelled / timed out           | Right                     |
| `nav_blip`        | You moved between cells (NOTEBOOK navigation), or the settings / menu cursor stepped | Overhead                  |
| `focus_shift`     | You changed focus mode (NOTEBOOK ↔ INPUT / OUTPUT / SETTINGS) or stepped to a new output line | Centre                    |
| `menu_open` / `menu_close` | The action menu opened / closed     | Overhead                  |
| `clipboard`       | Something was copied                          | Slightly overhead         |
| `tui_menu_alert`  | An interactive TUI menu was detected in output| Centre                    |
| `settings_chime`  | A settings edit succeeded                     | Overhead                  |
| `settings_save`   | The bank was saved to disk                    | Overhead                  |
| `tick` / `soft_tick` | Small lifecycle events (cell created, etc.)| Left                     |

Three narrator voices do all the speech:

* **narrator** (slightly left): normal output, notebook narration.
* **alert** (slightly right, higher pitch): stderr, command failures,
  error readouts.
* **system** (overhead, slightly elevated): session and cell
  lifecycle, settings, meta-events.

The left/right split is the main navigation aid: if you hear it on
the right, something wants your attention. If it's overhead, it's
about the terminal itself rather than about your command.

---

## Troubleshooting

**`[asat] cannot start: ASAT needs an interactive terminal (a TTY).`**
Something is piping your stdin into ASAT — maybe `echo :quit | python
-m asat`, a CI runner, or a sandbox without a real console. Launch
from a real terminal instead. Exit code 2.

**I launched and heard nothing.**
Run `python -m asat --check`. The header prints the sink class, bank,
session, and TTY state; then the four-step self-test runs. If step 4
reports `MemorySink active`, you forgot `--live` (Windows) or
`--wav-dir DIR` (any platform). If you passed `--live` and step 4
still reports `MemorySink`, the live backend is unavailable on this
host (POSIX today; tracked as F6) and `--check` exits non-zero.

### Diagnosing audio issues

`python -m asat --check` is the single command that answers "does
this install actually work?". It builds the Application like a real
launch — same bank, same sink resolution, same TTS backend — then
runs four steps and prints a `PASS / FAIL / SKIP` line per step.
Exit code 0 means every step passed; non-zero means at least one
step needs your attention. The steps are:

1. **`bank_validates`** — `default_sound_bank().validate()`. Catches
   structural issues (duplicate ids, bindings pointing at missing
   voices or sounds). Almost always passes for the built-in bank;
   fails on a hand-edited `--bank` JSON file with a typo.
2. **`voices_speak`** — every voice in the bank synthesises a short
   "voice <id> check" through the real TTS engine onto the active
   sink. Fails when a TTS backend is missing, a voice profile is
   broken, or the bank defines no voices. Skipped if step 1 failed.
3. **`event_cues`** — every covered event type is published with the
   representative payload from `asat/sample_payloads.py`; the step
   confirms at least one buffer lands on the sink for each. Fails
   when a binding's predicate has drifted from the payload shape
   (so the event arrives but no audio comes out). Skipped if step 1
   failed.
4. **`live_playback`** — reports the resolved sink. Passes
   informationally when `MemorySink` is active without `--live`
   (you asked for the silent default and got it); fails when
   `--live` was requested but the host fell back to `MemorySink`
   (no live backend available on this platform).

`SELF_CHECK_STEP` events are published on the bus as the run
progresses, so combining `--check` with `--log /tmp/asat.jsonl`
captures the full diagnostic for sharing in a bug report.

**I pressed a key and nothing happened.**
Check your mode. Most keys are mode-scoped; Ctrl+O in INPUT mode
just types literally because INPUT mode accepts characters. When in
doubt, type `:help` + Enter.

**I lost my command in the middle of typing and now I can't get back
to the input line.**
Press Escape from wherever you are (twice if you're in the settings
editor). You'll be in NOTEBOOK mode. Up / Down to find the cell you
were typing in, Enter to resume INPUT mode on it — the buffer you
typed is still there. If you are completely unsure where you are,
drop into INPUT mode and type `:help` + Enter.

**Everything is too chatty.**
Open `:settings`, navigate to the offending binding, press `e` on
`enabled`, type `false`, Enter, then Ctrl+S. The binding is silenced
but still in the bank so you can re-enable it later.

**I want to reuse a different existing sound for an event.**
Open `:settings`, walk to `bindings`, find the binding you want to
retune, descend to its `sound_id` field, press `e`, type the id of
another `SoundRecipe` in the bank, Enter, Ctrl+S.

**I want a brand-new sound that does not yet exist in the bank.**
The in-terminal editor today can retune existing records but cannot
create new voices, sounds, or bindings (see
[FEATURE_REQUESTS.md](FEATURE_REQUESTS.md) F2). To add one now: edit
the on-disk bank JSON directly, add a `SoundRecipe` object (give it
an `id`, `kind`, and `params`), update the relevant binding's
`sound_id` to your new id, then reopen ASAT — the new bank loads on
start-up.

**Output is too fast to follow.**
The `narrator` voice's `rate` field is a multiplier. Open
`:settings`, navigate to `voices` → `narrator` → `rate`, press `e`,
type `0.8` (or wherever you're comfortable), Enter, Ctrl+S.

**I want an audio cue whenever the cursor jumps on the screen.**
The `ANSI_CURSOR_MOVED` event fires on every cursor move. In
`:settings`, add a new binding with `event_type =
ansi.cursor.moved`, a short sound recipe, and an optional predicate
like `reason == "absolute"` to only react to full jumps.

---

## Where to go next

* [CHEAT_SHEET.md](CHEAT_SHEET.md) — single-page reference: every
  binding, meta-command, and audio cue that ships today.
* [SMOKE_TEST.md](SMOKE_TEST.md) — scripted hands-on walkthrough
  with expected narrations. Run it after installing, or after
  any non-trivial bank or settings change.
* [ARCHITECTURE.md](ARCHITECTURE.md) — what lives in which module.
* [EVENTS.md](EVENTS.md) — every event on the bus and its payload.
* [AUDIO.md](AUDIO.md) — full reference for voices, recipes,
  bindings, predicates, templates, the engine, and the editor.
* [CLAUDE_CODE_MODES.md](CLAUDE_CODE_MODES.md) — reference for the
  Claude Code CLI's interactive surfaces (useful when writing
  bindings that sonify Claude running inside ASAT).
