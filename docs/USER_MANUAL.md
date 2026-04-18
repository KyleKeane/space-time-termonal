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

## The five-minute tour

1. **Launch** ASAT. You'll hear a short rising chime (the session
   start) from directly overhead — that means the session is live.
2. **Type a command** — e.g. `dir`, `python --version`, `git status`.
   You are automatically in INPUT mode, so keys go into the current
   cell.
3. **Press Enter**. You'll hear a triangle-wave "submit" cue on the
   left, then the narrator reads output lines as they stream, then a
   major-chord "success" chime on Enter's exit code, or a low minor
   "failure" chord on the right if the command failed.
4. **Press Escape**. You're now in NOTEBOOK mode. Up / Down walks
   between cells.
5. **Press Ctrl+O**. You're now in OUTPUT mode, stepping line-by-line
   through the selected cell's captured output. Escape leaves.
6. **Press Ctrl+,** (or type `:settings` then Enter in INPUT mode).
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
`focus_shift` cue plus the narrator naming the new mode. If you lose
track of where you are, just press Escape — it always takes you one
level out (INPUT / OUTPUT → NOTEBOOK, SETTINGS → close), and the
narrator confirms the new mode.

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

Moving between cells plays the `nav_blip` cue. The narrator reads
either "new cell" (empty) or the first line of the cell's command so
you know what you're standing on.

---

## INPUT mode — typing commands

You're in INPUT mode the moment you start typing in a fresh cell,
or when you press Enter from NOTEBOOK mode.

| Key       | What it does                                      |
|-----------|---------------------------------------------------|
| Any char  | Appended to the input buffer.                     |
| Backspace | Removes the last character.                       |
| Enter     | Submits the command to the execution kernel.      |
| Escape    | Commits the buffer into the cell and returns to NOTEBOOK (does not run). |

### Meta-commands

Any line starting with `:` is a **meta-command**. These are
intercepted before the kernel ever sees them — the buffer is
discarded and the cell is not modified.

| Meta-command | Effect                                               |
|--------------|------------------------------------------------------|
| `:settings`  | Open the settings editor (same as Ctrl+,).           |
| `:save`      | Save the current session to disk.                    |
| `:quit`      | Exit ASAT.                                           |

Type the meta-command exactly as shown, then press Enter. If you
mistype (e.g. `:setings`), the line is treated as a normal command
and handed to the shell — you'll hear the failure chord back.

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
| Escape      | Return to NOTEBOOK mode.                          |

Each step plays the `focus_shift` cue and the narrator reads the
focused line. Stderr lines come from the alert voice on the right;
stdout lines come from the narrator on the left — the spatial split
tells you which stream a line came from without the narrator having
to say "stderr:" every time.

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
| INPUT      | Enter             | Submit command                        |
| INPUT      | Backspace         | Delete last char                      |
| INPUT      | Escape            | Leave INPUT without running           |
| INPUT      | `:settings`⏎      | Open settings editor                  |
| INPUT      | `:save`⏎          | Save session                          |
| INPUT      | `:quit`⏎          | Exit ASAT                             |
| OUTPUT     | Up / Down         | Prev / next line                      |
| OUTPUT     | PageUp / PageDown | Jump a page                           |
| OUTPUT     | Home / End        | First / last line                     |
| OUTPUT     | Escape            | Back to NOTEBOOK                      |
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
| `nav_blip`        | You moved the notebook cursor                 | Overhead                  |
| `focus_shift`     | You changed mode or focused a new output line | Centre                    |
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

**I pressed a key and nothing happened.**
Check your mode. Most keys are mode-scoped; Ctrl+O in INPUT mode
just types literally because INPUT mode accepts characters.

**I lost my command in the middle of typing and now I can't get back
to the input line.**
Press Escape from wherever you are (twice if you're in the settings
editor). You'll be in NOTEBOOK mode. Up / Down to find the cell you
were typing in, Enter to resume INPUT mode on it — the buffer you
typed is still there.

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
