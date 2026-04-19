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
- **Sanity-check your install:** `python -m asat --check`. Builds the
  Application, prints the picked sink, bank, session, and TTY state,
  and exits without starting the read loop. Useful when you launched
  once and heard nothing — `--check` tells you which sink you got.

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

## Focus modes

You are always in exactly one of four modes. The current mode
decides what keys do.

| Mode       | What you're doing                                  |
|------------|----------------------------------------------------|
| `NOTEBOOK` | Walking between cells (no keys go into a buffer). |
| `INPUT`    | Typing a command into the active cell.             |
| `OUTPUT`   | Stepping line-by-line through captured output.     |
| `SETTINGS` | Driving the live SoundBank editor.                 |

Every mode transition announces itself: you'll hear the
`focus_shift` cue overhead and the system voice names the new mode
(`"input"`, `"notebook"`, `"output"`, `"settings"`). Walking between
cells within NOTEBOOK mode plays `nav_blip` instead and the narrator
reads the focused cell's command. Typing into the input buffer is
silent on purpose — the `insert_character` action echoes the literal
character instead. If you lose track of where you are, just press
Escape — it always takes you one level out (INPUT / OUTPUT →
NOTEBOOK, SETTINGS → close), and the narrator confirms the new mode.

---

## NOTEBOOK mode — walking cells

| Key        | What it does                                      |
|------------|---------------------------------------------------|
| Up / Down  | Previous / next cell.                             |
| Home / End | First / last cell.                                |
| Enter      | Enter INPUT mode on the focused cell.             |
| Ctrl+N     | Create a new empty cell below.                    |
| Ctrl+O     | Enter OUTPUT mode on the focused cell's output.   |
| Ctrl+,     | Open the settings editor.                         |
| `d`        | Delete the focused cell.                          |
| `y`        | Duplicate the focused cell (inserts a copy below).|
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
| Ctrl+A / Ctrl+E | Alias for Home / End (readline-style).       |
| Ctrl+W    | Delete the word immediately before the caret.     |
| Ctrl+U    | Delete from the start of the line up to the caret. |
| Ctrl+K    | Delete from the caret to the end of the line.    |
| Enter     | Submits the command to the execution kernel.      |
| Escape    | Commits the buffer into the cell and returns to NOTEBOOK (does not run). |

### Meta-commands

Any line starting with `:` is a **meta-command**. These are
intercepted before the kernel ever sees them — the buffer is
discarded and the cell is not modified.

| Meta-command | Effect                                               |
|--------------|------------------------------------------------------|
| `:help`      | Narrate + print the keystroke cheat sheet.           |
| `:settings`  | Open the settings editor (same as Ctrl+,).           |
| `:save`      | Save the current session to `--session` path (no-op without one). |
| `:quit`      | Exit ASAT.                                           |
| `:delete`    | Delete the focused cell (same as `d` in NOTEBOOK).   |
| `:duplicate` | Duplicate the focused cell (same as `y` in NOTEBOOK).|
| `:pwd`       | Announce the current working directory.              |
| `:commands`  | List every available meta-command.                   |

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

Cancelling a running command is a future keyboard binding; for now
let commands finish or send `:quit` to bail out of the whole session.

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
| Ctrl+S              | Save the bank to disk.                          |
| Ctrl+Q              | Close the editor.                               |

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
| NOTEBOOK   | F2 / Ctrl+.       | Open actions menu                     |
| INPUT      | Enter             | Submit command                        |
| INPUT      | Backspace         | Delete char before caret              |
| INPUT      | Delete            | Delete char under caret               |
| INPUT      | Left / Right      | Move caret one character              |
| INPUT      | Home / End        | Caret to start / end                  |
| INPUT      | Ctrl+A / Ctrl+E   | Caret to start / end (readline)       |
| INPUT      | Ctrl+W            | Delete word before caret              |
| INPUT      | Ctrl+U            | Delete from start of line to caret    |
| INPUT      | Ctrl+K            | Delete from caret to end of line      |
| INPUT      | Escape            | Leave INPUT without running           |
| INPUT      | F2 / Ctrl+.       | Open actions menu                     |
| INPUT      | `:help`⏎          | Narrate + print the cheat sheet       |
| INPUT      | `:settings`⏎      | Open settings editor                  |
| INPUT      | `:save`⏎          | Save session                          |
| INPUT      | `:quit`⏎          | Exit ASAT                             |
| INPUT      | `:delete`⏎        | Delete focused cell                   |
| INPUT      | `:duplicate`⏎     | Duplicate focused cell                |
| INPUT      | `:pwd`⏎           | Announce working directory            |
| INPUT      | `:commands`⏎      | List every meta-command               |
| OUTPUT     | Up / Down         | Prev / next line                      |
| OUTPUT     | PageUp / PageDown | Jump a page                           |
| OUTPUT     | Home / End        | First / last line                     |
| OUTPUT     | `/`               | Search (live), Enter commits, Escape restores |
| OUTPUT     | `n` / `N`         | Next / previous search match          |
| OUTPUT     | `g<number>` Enter | Jump to 1-based line number           |
| OUTPUT     | Escape            | Back to NOTEBOOK                      |
| OUTPUT     | F2 / Ctrl+.       | Open actions menu                     |
| MENU       | Up / Down         | Prev / next item                      |
| MENU       | Enter             | Invoke focused item                   |
| MENU       | Escape            | Close without invoking                |
| SETTINGS   | Up / Down         | Prev / next item                      |
| SETTINGS   | Right / Enter     | Descend                               |
| SETTINGS   | Left / Escape     | Ascend / close                        |
| SETTINGS   | `e`               | Begin edit (at field level)           |
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
Run `python -m asat --check`. It prints the sink class that was picked,
the bank path, and whether stdin is a TTY. If the sink is `MemorySink`,
you forgot `--live` (Windows) or `--wav-dir DIR` (any platform).

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

* [ARCHITECTURE.md](ARCHITECTURE.md) — what lives in which module.
* [EVENTS.md](EVENTS.md) — every event on the bus and its payload.
* [AUDIO.md](AUDIO.md) — full reference for voices, recipes,
  bindings, predicates, templates, the engine, and the editor.
* [CLAUDE_CODE_MODES.md](CLAUDE_CODE_MODES.md) — reference for the
  Claude Code CLI's interactive surfaces (useful when writing
  bindings that sonify Claude running inside ASAT).
