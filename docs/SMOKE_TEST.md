# ASAT hands-on smoke test

A scripted, end-to-end walkthrough designed to be followed
keystroke-by-keystroke by a blind user. Each step names exactly
what to press, what narration or cue to expect, and a fallback
("**If not:** …") that points at the likely cause when the cue
is wrong or missing.

The script takes about 10 minutes if every step matches. It
covers the golden path plus the "how do I get unstuck" reflexes
(Escape, `:help`, `:state`, Ctrl+R) so the first time any of
those is needed later isn't the first time they've been tried.

Terminology:

- **Cue** — a non-verbal sound from the default bank (a chime,
  a blip, a chord). The *where* column in CHEAT_SHEET.md's sound
  lexicon tells you which side of the stereo field to listen on.
- **Narration** — a spoken line. Text in `"` is the exact
  (or near-exact) phrase the TTS should read.
- **Text trace** — the stdout line printed alongside the audio
  (unless you launched with `--quiet`).

Quote style: *"like this"* means a TTS line; `like this` means
something you type or a code identifier.

---

## Pre-flight

### 0.1 Install sanity check

```
python -m asat --check
```

**Expected stdout.** Four lines, in order:

1. `[asat] sink=` followed by the name of the audio backend
   that will be used (`WinMMSink` on Windows `--live`,
   `WavDirSink` on `--wav-dir`, `MemorySink` for the default
   in-memory path).
2. `[asat] bank=` naming the loaded sound bank (empty string for
   the built-in default).
3. `[asat] session=` naming the session path (empty string for
   a transient session).
4. `[asat] tty=` either `yes` or `no`.

**If `sink=MemorySink`**. You'll hear nothing on the speaker
when you launch for real. Add `--live` (Windows) or
`--wav-dir /tmp/asat` (POSIX) for the rest of the test.

**If `tty=no`**. You are running inside a sandbox or piped
stdin. Launch from a real terminal — ASAT will refuse to start
the read loop otherwise.

---

## Act 1 — Launch

### 1.1 Start the session

Windows:
```
python -m asat --live
```
POSIX:
```
python -m asat --wav-dir /tmp/asat
```

**Expected audio (in order, within one second).**

1. **`session_chime`** — a short rising chime, directly
   overhead. This is SESSION_CREATED firing.
2. **`focus_shift`** — a centre cue as the cursor drops into
   INPUT mode on the first empty cell.

**Expected narration.** Two system-voice lines, overhead:

1. *"session <short-id> ready. Type colon help for the keystroke
   cheat sheet, colon quit to exit."*
2. *"input cell one"* (or similar — a terse "you're in INPUT"
   confirmation).

**Expected text trace (unless `--quiet`).**

```
[asat] session <id> ready. Type :help for the keystroke cheat
sheet, :quit to exit.
[input #<short-id>]
```

**If you hear the chime but no TTS.** The voices are muted.
Open settings (Ctrl+,), walk to `voices` → `narrator` →
`enabled`, press `e`, type `true`, Enter, Ctrl+S.

**If you hear nothing at all.** Re-run `--check`; sink is
probably `MemorySink`.

### 1.2 First-run welcome (first launch only)

On the very first ASAT launch on this machine (sentinel at
`~/.asat/first-run-done` is absent) you additionally hear:

- A narration *"Welcome to ASAT. Type colon help to hear the
  keystroke cheat sheet."* — narrator voice, left.
- A four-line spoken tour spelling the key meta-commands
  letter by letter (so TTS pronounces `:help` as `h, e, l, p`).

**To replay later.** Type `:welcome` then Enter in INPUT mode.

**To reset.** Delete `~/.asat/first-run-done` and re-launch.

---

## Act 2 — Run a successful command

### 2.1 Type and submit

Type:
```
echo hello world
```

**Per-keystroke expectation.** Each printable character fires a
KEY_PRESSED event. The default bank has no per-keystroke cue,
so you should hear nothing until you press Enter. (If you do
hear a cue per key, `sounds.keystroke` got enabled somewhere;
disable it.)

Press **Enter**.

**Expected cues (in order).**

1. **`submit`** — triangle-wave cue on the left.
2. **`start`** — kernel begin; left.
3. The narrator reads output lines as they stream
   (*"hello world"*, left-centre).
4. **`success_chord`** — major chord on the left when the
   command exits 0.
5. **`focus_shift`** — centre, as the cursor auto-advances to a
   fresh INPUT cell.

**Expected text trace.** `[ok] echo hello world` (or similar
"exit 0" framing), then the output line, then a new
`[input #…]` banner.

**If the chord plays on the right instead of the left.** The
command actually failed. Check what was typed — trailing
whitespace or a shell mismatch are the usual culprits. The next
act deliberately forces this path.

### 2.2 Confirm the auto-advance

Without pressing any other key, type:
```
:pwd
```
and press **Enter**.

**Expected narration.** *"Working directory: <your cwd>"* —
system voice, overhead. This confirms you really were in INPUT
mode on a fresh cell and that meta-commands are routing.

---

## Act 3 — Run a failing command

### 3.1 A command that exits non-zero

Type (pick one your shell will reject):
```
this-command-does-not-exist
```

Press **Enter**.

**Expected cues.** `submit` + `start` on the left as before;
then **`failure_chord`** on the **right** (alert spatial
region — this is the "wants your attention" side). Stderr text
is read by the alert voice, also right-biased.

**If you hear the success chord instead.** Your shell silently
created an alias or your PATH has something that matches —
replace the command with `false` (POSIX) or `exit 1` (cmd) and
re-run.

---

## Act 4 — Navigate the notebook

### 4.1 Leave INPUT, walk the history

Press **Escape**.

**Expected.** Centre `focus_shift` + narration *"notebook,
cell N"* (N being the cell you're sitting on).

Press **Up**.

**Expected.** Overhead `nav_blip` + narrator reads the command
of the previous cell (*"echo hello world"*).

Press **Up** again.

**Expected.** Same cue + the narrator reads the prior cell's
command (`this-command-does-not-exist`).

Press **Down** to return.

### 4.2 "Where am I?" reflex

From NOTEBOOK, press **Enter** to drop into INPUT on the
focused cell. Type:
```
:state
```
Press **Enter**.

**Expected narration.** Four lines, system voice, overhead:

1. *"Focus mode: input"*
2. *"Position: cell <index> of <total>"*
3. *"Session id: <id>"*
4. *"Working directory: <cwd>"*

This is the one command to memorise. Any time you lose track,
`:state` answers all four questions in one keystroke.

---

## Act 5 — Explore captured output

### 5.1 Generate multi-line output

From INPUT mode, type a command whose output spans several
lines. A portable option:
```
python -c "for i in range(20): print('line', i)"
```

Press **Enter**.

**Expected.** The narrator reads each line as it streams; the
success chord fires on exit.

### 5.2 Enter OUTPUT mode

Press **Escape** to leave INPUT. Press **Up** if needed to
focus the cell you just ran. Press **Ctrl+O**.

**Expected.** Centre `focus_shift` + narration *"output, line
20 of 20"* (or whichever line count fits). OUTPUT mode snaps to
the last captured line, which is usually what you want.

Press **Up** several times.

**Expected.** Per press: narrator reads the previous line,
`focus_shift` cue.

Press **Home**.

**Expected.** Snap to line 1 + narrator reads it.

### 5.3 Search within output

Press **`/`**.

**Expected.** Narration *"search"* + system cue indicating the
composer is open. Typing now narrows a live search.

Type `line 1`.

**Expected.** Per keystroke, the cursor jumps to the first
match; the narrator reads that line.

Press **Enter** to commit.

**Expected.** Composer closes; cursor stays on the commit line.
Press **`n`** to cycle to the next match, **`N`** for previous.

Press **Escape** to leave the composer (or leave OUTPUT mode
entirely).

### 5.4 Goto line

Still in OUTPUT mode, press **`g`**. Type `5`. Press **Enter**.

**Expected.** Cursor snaps to line 5 + narrator reads it.

Press **Escape** to return to NOTEBOOK.

---

## Act 6 — Meta-commands and discoverability

From NOTEBOOK, press **Enter** to drop into INPUT on an empty
cell (or append one with Ctrl+N first if the current cell is
populated).

### 6.1 `:help` and `:commands`

Type `:help` + Enter.

**Expected.** Narrator reads a terse keystroke cheat sheet;
the full sheet also prints to the text trace.

Type `:commands` + Enter.

**Expected.** Narrator enumerates every meta-command in the
router (should include `:state`, `:pwd`, `:repeat`, …). If
anything is missing from the list, `META_COMMANDS` in
`asat/input_router.py` drifted from the dispatch table.

### 6.2 Typo forgiveness

Type `:setings` + Enter. (Deliberate typo.)

**Expected.** Narration *"unknown meta-command `:setings` — did
you mean `:settings`?"*. You stay in INPUT mode; the buffer
clears so you can retype cleanly.

### 6.3 Replay

Press **Ctrl+R**.

**Expected.** The last narration re-plays verbatim. (From INPUT,
Ctrl+R is `:repeat`.) Useful when someone speaks over you or an
event fires faster than you could parse it.

---

## Act 7 — Settings editor

### 7.1 Open, navigate, close

Press **Escape** to return to NOTEBOOK. Press **Ctrl+,** (comma).

**Expected.** `focus_shift` + narration *"settings. voices, 3
items."* You're at the top of the editor on the `voices`
section.

Press **Down**.

**Expected.** `nav_blip` + *"sounds, 17 items."* (numbers may
vary with your bank).

Press **Down** once more, then **Up** twice, to confirm
navigation works forwards and backwards.

Press **Right** (or **Enter**) to descend into the current
section.

**Expected.** *"<first record name>"* — you're now at the
record level.

Press **Left** (or **Escape**) to ascend.

Press **Ctrl+Q** (or Escape from the top level) to close.

**Expected.** Return to NOTEBOOK; `focus_shift` cue.

### 7.2 Edit a field

Reopen settings (Ctrl+,). Press **Down** to reach `sounds`,
**Right** to descend, **Down**/**Up** to navigate records,
**Right** to descend into a record's fields, then walk to any
editable field (e.g. `gain` or `enabled`).

Press **`e`** to begin editing.

**Expected.** Narration *"editing <field name>, current value
<value>"*.

Type a new value (e.g. `0.5` for gain). Press **Enter** to
commit.

**Expected.** `settings_chime` overhead + narration *"<field>
set to <value>"*.

Press **Ctrl+Z** to undo.

**Expected.** Field reverts; narration confirms.

Press **Ctrl+S** to save (only useful if you launched with
`--bank path.json`; a no-op otherwise).

Press **Ctrl+Q** to close.

### 7.3 Search across settings

Re-open settings. Press **`/`**. Type `gain`. Press **Enter**.

**Expected.** Narrator reports match count; cursor jumps to the
first field containing "gain". **`n`** / **`N`** cycle through
matches.

Press **Escape** once to close the composer, again to close
settings.

---

## Act 8 — Actions menu

From NOTEBOOK (on a cell), press **F2** (or **Ctrl+.**).

**Expected.** `menu_open` overhead + narration *"actions:
<first item>"*. Default items include copy-command, copy-output,
etc.

Press **Down** / **Up** to walk the list. Press **Enter** to
invoke the focused action (most actions fire `clipboard` and
announce what was copied).

Press **Escape** instead of Enter to close without invoking.

**Expected.** `menu_close` cue.

---

## Act 9 — Save, resume, quit

### 9.1 Save mid-session

If you launched with `--session work.json`: type `:save` +
Enter.

**Expected.** `settings_save` (overhead) + narration
*"session saved to work.json"*. If you did **not** launch with
`--session`, `:save` is a silent no-op.

### 9.2 Quit

Type `:quit` + Enter (or press the binding configured for
quit).

**Expected.** Narration *"goodbye"* (or equivalent closing
line), then the process exits. If you launched with
`--session`, the file is re-written on exit.

### 9.3 Resume

Re-launch with the same `--session work.json`.

**Expected.** In addition to the normal session-start
narration, a *"session loaded from work.json"* line and all
your cells are present. Press Up from NOTEBOOK to walk them.

---

## Red-flag checklist

Run this list at the end. If any line has a mismatch, capture a
log (`python -m asat --log /tmp/events.jsonl --live`) and file
it with the HANDOFF.

| # | Check | Expected |
|---|-------|----------|
| 1 | Launch plays a chime. | Yes. |
| 2 | First keystroke in INPUT types silently. | Yes, no per-keystroke cue. |
| 3 | `echo hello` chord is on the **left**. | Yes. |
| 4 | A failing command chord is on the **right**. | Yes. |
| 5 | Escape from INPUT lands on NOTEBOOK. | Yes. |
| 6 | Up / Down reads the previous / next cell's command. | Yes. |
| 7 | Ctrl+O reads line N-of-N for the focused cell. | Yes. |
| 8 | `/` in OUTPUT narrows live as you type. | Yes. |
| 9 | `:state` names mode + position + cwd + session id. | Yes. |
| 10 | `:commands` includes `:state`, `:pwd`, `:repeat`. | Yes. |
| 11 | `:setings` (typo) narrates a "did you mean" hint. | Yes. |
| 12 | Ctrl+R replays the last narration unchanged. | Yes. |
| 13 | Ctrl+, opens settings; Ctrl+Q closes it. | Yes. |
| 14 | F2 opens the actions menu; Escape closes it. | Yes. |
| 15 | `:quit` cleanly exits. | Yes. |

Any "No" is a bug report worth writing.

---

## If you get completely lost

1. **Escape** (twice if in settings or a sub-composer) —
   you'll end up in NOTEBOOK.
2. **Enter** — drops you into INPUT on the focused cell, with
   its buffer preserved.
3. Type **`:state`** + Enter — ASAT names where you are.
4. Type **`:help`** + Enter — full cheat sheet.
5. Worst case: Ctrl+C in the shell. The session (if
   `--session` was passed) is still written on exit.
