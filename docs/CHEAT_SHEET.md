# ASAT cheat sheet

One page. Every binding, every meta-command, every audio cue
that ships today. For prose explanation see
[USER_MANUAL.md](USER_MANUAL.md).

---

## Launch

| Recipe                                  | When to use it                          |
|-----------------------------------------|-----------------------------------------|
| `python -m asat --live`                 | **Windows**: live audio on the speaker. |
| `python -m asat --wav-dir /tmp/asat`    | **POSIX**: write each rendered buffer to WAV. |
| `python -m asat --check`                | Smoke-test install; prints sink/bank/session and exits. |
| `python -m asat --quiet`                | Suppress stdout text trace; audio only. |
| `python -m asat --bank mybank.json`     | Start from a saved SoundBank.           |
| `python -m asat --session work.json`    | Resume / persist a session at this path.|
| `python -m asat --log events.jsonl`     | Write one JSON line per bus event.      |
| `python -m asat --version`              | Print version and exit.                 |

Flags compose freely. `--check` is the one to reach for if you
launched and heard nothing.

---

## Focus modes

You are always in exactly one of four modes. Mode decides what
keys do.

| Mode       | Purpose                                            |
|------------|----------------------------------------------------|
| `NOTEBOOK` | Walk between cells.                                |
| `INPUT`    | Type a command into the focused cell.              |
| `OUTPUT`   | Step line-by-line through captured output.         |
| `SETTINGS` | Drive the live SoundBank editor.                   |

A short menu sub-mode also exists, opened from NOTEBOOK / INPUT
/ OUTPUT via F2 (or Ctrl+. as a fallback). Escape always
escapes one level.

---

## NOTEBOOK mode

| Key                | Action                                          |
|--------------------|-------------------------------------------------|
| Up / Down          | Previous / next cell.                           |
| Home / End         | First / last cell.                              |
| Enter              | Drop into INPUT mode on the focused cell.       |
| Ctrl+N             | New empty cell below.                           |
| Ctrl+O             | Step into OUTPUT mode for this cell.            |
| Ctrl+,             | Open settings editor.                           |
| Ctrl+R             | Repeat the most recent narration.               |
| `d`                | Delete the focused cell.                        |
| `y`                | Duplicate the focused cell.                     |
| Alt+Up / Alt+Down  | Move the focused cell up / down.                |
| F2 / Ctrl+.        | Open the actions menu.                          |

---

## INPUT mode

| Key                | Action                                          |
|--------------------|-------------------------------------------------|
| Any printable char | Insert at caret.                                |
| Backspace / Delete | Delete char before / under caret.               |
| Left / Right       | Move caret one character.                       |
| Home / End         | Jump caret to start / end.                      |
| Ctrl+A / Ctrl+E    | Alias for Home / End (readline-style).          |
| Ctrl+W             | Delete word before caret.                       |
| Ctrl+U             | Delete from start of line to caret.             |
| Ctrl+K             | Delete from caret to end of line.               |
| Enter              | Submit command to the kernel.                   |
| Escape             | Commit buffer to cell, return to NOTEBOOK.      |
| Ctrl+R             | Repeat the most recent narration.               |
| F2 / Ctrl+.        | Open the actions menu.                          |

---

## OUTPUT mode

| Key                | Action                                          |
|--------------------|-------------------------------------------------|
| Up / Down          | Previous / next line.                           |
| PageUp / PageDown  | Jump a page.                                    |
| Home / End         | First / last captured line.                     |
| `/`                | Live search; Enter commits, Escape restores.    |
| `n` / `N`          | Cycle to next / previous match (after commit).  |
| `g`                | Jump to 1-based line number; Enter commits.     |
| Escape             | Return to NOTEBOOK mode.                        |
| F2 / Ctrl+.        | Open the actions menu.                          |

---

## SETTINGS mode

Open with `Ctrl+,` from NOTEBOOK or `:settings` from INPUT.
Close with `Ctrl+Q` or Escape at the top level.

| Key                | Action                                          |
|--------------------|-------------------------------------------------|
| Up / Down          | Previous / next item at current level.          |
| Right / Enter      | Descend (section → record → field).             |
| Left / Escape      | Ascend (or close at top level).                 |
| `e`                | Begin editing the focused field.                |
| `/`                | Cross-section search; Enter commits.            |
| `n` / `N`          | Next / previous search match (after commit).    |
| Ctrl+Z / Ctrl+Y    | Undo / redo edits.                              |
| Ctrl+R             | Reset to defaults at cursor scope.              |
| Ctrl+S             | Save bank to disk.                              |
| Ctrl+Q             | Close the editor.                               |

In edit sub-mode (after `e`): printable chars compose the new
value, Backspace trims, Enter commits, Escape cancels.

---

## ACTIONS MENU sub-mode

| Key       | Action                                                |
|-----------|-------------------------------------------------------|
| Up / Down | Previous / next item (clamped at the ends).           |
| Enter     | Invoke focused item; menu closes.                     |
| Escape    | Close without invoking.                               |

---

## Meta-commands (INPUT mode, line begins with `:`)

Case-insensitive. A trailing argument is allowed
(`:help settings`).

| Command           | Effect                                                 |
|-------------------|--------------------------------------------------------|
| `:help`           | Narrate + print the keystroke cheat sheet.             |
| `:help topics`    | List every focused `:help <topic>` micro-tour.         |
| `:help <topic>`   | Narrate one tour: `navigation`, `cells`, `settings`, `audio`, `search`, `meta`. |
| `:settings`       | Open the settings editor (same as Ctrl+,).             |
| `:save`           | Save the current session to `--session` path.          |
| `:quit`           | Exit ASAT.                                             |
| `:delete`         | Delete the focused cell (same as `d` in NOTEBOOK).     |
| `:duplicate`      | Duplicate the focused cell (same as `y` in NOTEBOOK).  |
| `:pwd`            | Announce the current working directory.                |
| `:commands`       | List every available meta-command.                     |
| `:reset bank`     | Reset the entire sound bank to defaults (alias `:reset all`). |
| `:welcome`        | Replay the first-run welcome tour.                     |
| `:repeat`         | Re-hear the most recent narration (same as Ctrl+R).    |

A mistype like `:setings` clears the buffer, narrates a hint
("did you mean `:settings`?"), and stays in INPUT mode.

---

## Sound lexicon

What the default bank plays, where in space, and when. Voices
narrate from the same spatial regions.

| Cue              | When                                                | Where             |
|------------------|-----------------------------------------------------|-------------------|
| `session_chime`  | A new session starts.                               | Overhead          |
| `submit`         | You pressed Enter in INPUT mode.                    | Left              |
| `start`          | Kernel began running the command.                   | Left              |
| `success_chord`  | Command exited 0.                                   | Left              |
| `failure_chord`  | Command exited non-zero.                            | Right             |
| `cancel`         | Command cancelled / timed out.                      | Right             |
| `nav_blip`       | Cell navigation; settings / menu cursor stepped.    | Overhead          |
| `focus_shift`    | Mode change, or new line stepped in OUTPUT.         | Centre            |
| `menu_open`      | Actions menu opened.                                | Overhead          |
| `menu_close`     | Actions menu closed.                                | Overhead          |
| `clipboard`      | Something was copied.                               | Slightly overhead |
| `tui_menu_alert` | Interactive TUI menu detected in output.            | Centre            |
| `settings_chime` | A settings edit succeeded.                          | Overhead          |
| `settings_save`  | Bank was saved to disk.                             | Overhead          |
| `tick` / `soft_tick` | Small lifecycle events (cell created, etc.).    | Left              |

| Voice      | Where             | What it speaks                              |
|------------|-------------------|---------------------------------------------|
| `narrator` | Slightly left     | Normal output; notebook narration.          |
| `alert`    | Slightly right    | Stderr; failures; error read-outs.          |
| `system`   | Overhead          | Session / cell lifecycle; settings; meta.   |

Spatial split is the navigation aid: **right = wants your
attention; overhead = about the terminal itself.**

---

## When something doesn't work

| Symptom                                       | Try                                                                                  |
|-----------------------------------------------|--------------------------------------------------------------------------------------|
| Launched, heard nothing.                      | `python -m asat --check` — names the picked sink. If `MemorySink`, you forgot `--live`. |
| Pressed a key, nothing happened.              | Wrong mode. `:help` + Enter from INPUT prints the cheat sheet.                       |
| Lost in the middle of typing.                 | Escape (twice from settings) → NOTEBOOK → Up/Down to your cell → Enter resumes INPUT. The buffer is preserved. |
| Everything is too chatty.                     | `:settings` → walk to the offending binding → `e` on `enabled` → `false` → Ctrl+S.   |
| Output too fast.                              | `:settings` → `voices` → `narrator` → `rate` → `e` → `0.8` → Ctrl+S.                 |
| `cannot start: ASAT needs an interactive terminal`. | stdin isn't a TTY. Don't pipe input; launch from a real terminal.              |

Full troubleshooting in [USER_MANUAL.md](USER_MANUAL.md#troubleshooting).
