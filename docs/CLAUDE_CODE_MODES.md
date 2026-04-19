# Claude Code modes

Reference for the interactive surfaces the Claude Code CLI shows inside
a terminal. ASAT users drive Claude Code through an ASAT terminal cell,
so every surface Claude Code draws is what ASAT must sonify. This page
is a map of those surfaces keyed to the ASAT event vocabulary in
[EVENTS.md](EVENTS.md) and the binding model in [AUDIO.md](AUDIO.md).

Nothing here is ASAT-specific behaviour: this is descriptive reference
so bindings can target the right signals.

---

## Prompt / input modes

Claude Code's input line switches between several modes. Each mode
changes both what the user types and what the terminal draws.

| Mode | How entered | What it does | Visible cue |
|---|---|---|---|
| Default | idle | free-form prompt to Claude | `>` prompt, normal cursor |
| Multiline | `\` at end of line, or paste with newlines | keeps taking input until submit | `>` changes to `·` on continuation lines |
| Bash | prefix `!` | one-shot shell command, not sent to the model | `!` stays in the buffer, prompt turns red |
| Memory | prefix `#` | appends the line to project memory | `#` stays in the buffer, confirmation toast |
| Slash | prefix `/` | runs a slash command (see below) | autocomplete popup opens |
| Vim | `/vim` or vim config | modal editing (NORMAL / INSERT) | mode indicator in status line |
| History search | `Ctrl+R` | reverse-incremental prior prompt | separate one-line prompt |
| Paste | bracketed paste | captures multi-line paste as one chunk | "Pasted N lines" placeholder |

Raw ANSI signals worth binding:
- Alt-screen enter / leave: `CSI ?1049h` / `CSI ?1049l` → fires
  `ANSI_DISPLAY_CLEARED` with `mode=2` in ASAT's `TuiBridge`.
- Bracketed paste: `CSI ?2004h` / `CSI ?2004l` wrapping `CSI 200~` /
  `CSI 201~` around pasted data. Bracketed paste is visible only as
  `OUTPUT_LINE_APPENDED` with a synthetic "Pasted N lines" string.
- Semantic prompt markers (OSC 133): emitted by some shells around
  prompt / command / output regions; exposed as `ANSI_OSC_RECEIVED`
  with `category` set to one of `"prompt_start"`, `"prompt_end"`,
  `"command_start"`, `"command_end"` (or `"prompt"` for unknown OSC
  133 subcommands) and `body` starting `133`. The default bank ships
  a short blip on `category == "prompt_start"`; the other subcommands
  stay silent until users opt in.

---

## Approval dialogs

Claude Code blocks on a decision whenever it wants to run something
that the user has not pre-authorised. Every approval dialog has the
same shape — a bordered box with a numbered list and a default
highlighted row — but different triggers.

| Trigger | Title shown | Typical choices |
|---|---|---|
| Tool use (generic) | "Claude wants to use …" | allow once, always, deny |
| Bash command | "Run this command?" | allow once, always, deny, edit first |
| File write / edit | "Claude wants to edit …" | allow once, always, deny |
| MCP tool | "MCP tool request" | allow once, always, deny |
| Plan exit | "Apply this plan?" | yes, no, keep planning |
| Permission config change | "Update permissions?" | yes, no |
| Auto-mode block | "Auto-mode blocked" | allow once, cancel |

In the raw stream these show up as a reverse-video header line and a
list of options where the selected row is painted reverse-video too.
That is exactly the pattern `TuiBridge` already detects as an
interactive menu, so approval dialogs fire
`INTERACTIVE_MENU_DETECTED` / `_UPDATED` / `_CLEARED` with
`detection == "reverse_video"` and `items` being the option list.

---

## Slash commands

The built-in slash commands fall into a few families. The table lists
the observable surface, not the full command semantics.

| Command | Surface it opens |
|---|---|
| `/help` | static text block |
| `/clear` | alt-screen redraw (triggers display-cleared + screen-updated) |
| `/compact` | confirmation prompt → progress status → screen redraw |
| `/cost` | static text block |
| `/model` | approval-style menu of model IDs |
| `/config` | approval-style menu; entering a submenu pushes another menu |
| `/mcp` | list view with per-server status indicators |
| `/agents` | list view → edit form (multi-field) |
| `/install-github-app` | external-browser prompt + confirmation |
| `/login`, `/logout` | confirmation prompt |
| `/review` | inline diff block |
| `/resume` | list of sessions → selection |
| `/permissions` | nested approval-style menus |
| `/vim` | sets input mode (see above) |

Custom slash commands (user-defined) surface identically — the
distinction is only visible in the slash autocomplete popup, where
custom entries are grouped separately.

---

## Autocomplete surfaces

Three popup variants appear attached to the input line:

| Trigger | What it lists |
|---|---|
| `@` | files / folders under the project, fuzzy-matched |
| `@agent-<name>` | registered agents |
| `/` | slash commands (built-in + custom) |

All three render as a reverse-video selected row over a small list;
`TuiBridge` exposes them as `INTERACTIVE_MENU_*` events just like
approval dialogs. The `selected_index` payload field is how a binding
tells "moved up" from "moved down".

---

## Transient status indicators

Short-lived decorations that appear while Claude Code is working.
These are drawn with cursor-moves and in-place overwrites, not new
lines, so the ASAT binding that covers them should lean on ANSI
events, not `OUTPUT_LINE_APPENDED`.

| Indicator | How it looks | Useful ANSI signal |
|---|---|---|
| Thinking spinner | animated glyph + word ("Thinking…") | `ANSI_CURSOR_MOVED` with `reason=="column"` |
| Tool running | tool name + elapsed seconds | `ANSI_LINE_ERASED` each tick |
| Queued input | "N queued" pill | `ANSI_SGR_CHANGED` adding `reverse` |
| Network retry | "Retrying…" toast | `ANSI_BELL` on first retry |
| Token-usage meter | corner-pinned counter | OSC title updates (`ANSI_OSC_RECEIVED` `category=="title"`) |

None of these emit plain text lines that contain the status word, so
a binding that subscribes only to `OUTPUT_LINE_APPENDED` will miss
them. Subscribing to `ANSI_*` events is the reliable path.

---

## Diff / plan review surfaces

When Claude Code wants to show work before committing to it:

- **Diff view** — syntax-highlighted diff inline; scrollable when it
  exceeds the cell. Arrow keys and `j`/`k` move the viewport;
  those keystrokes surface as `KEY_PRESSED` events (intentionally
  unbound in the default bank).
- **Plan review** — before exiting plan mode, Claude Code prints the
  plan as Markdown and asks to apply. This is a regular output block
  followed by an approval dialog — so
  `OUTPUT_LINE_APPENDED` events carry the plan text and
  `INTERACTIVE_MENU_DETECTED` marks the decision point.

The cleanest sonification pattern is: read the whole plan aloud once
(`narrator` voice, low rate) on `INTERACTIVE_MENU_DETECTED` when the
immediately-preceding lines match a plan heading, then announce the
highlighted option via the standard menu bindings.

---

## Keyboard shortcuts

| Keys | Effect |
|---|---|
| `Enter` | submit |
| `Shift+Enter` | newline (multiline) |
| `Ctrl+C` | cancel current run; second press exits |
| `Ctrl+D` | EOF (exits on empty buffer) |
| `Ctrl+L` | clear screen |
| `Ctrl+R` | reverse history search |
| `Esc` | dismiss popup / cancel approval |
| `Tab` | accept autocomplete |
| `Shift+Tab` | toggle auto-accept edits mode |
| `↑` / `↓` | move through prompt history |

All of these flow through ASAT as `KEY_PRESSED` events. The default
bank leaves them unbound (otherwise navigation would be deafening); a
user who wants per-key ticks can add their own bindings with a
`key == "Shift+Tab"` predicate.

---

## Sonification matrix

Concrete suggestions for mapping the surfaces above onto the
`SoundBank` primitives documented in [AUDIO.md](AUDIO.md). Treat this
as a starting point, not a prescription — these are not shipped in the
default bank today.

| Surface | Event type | Voice | Cue (sound kind) | Template snippet |
|---|---|---|---|---|
| Default prompt ready | `ANSI_OSC_RECEIVED` (`category=="prompt_start"`) | system | short tone 990 Hz 30 ms | — |
| Multiline continuation | `OUTPUT_LINE_APPENDED` | — | tone 220 Hz 40 ms | — |
| Bash mode entered | `KEY_PRESSED` (`key=="!"`) | system | chord (440, 660) 80 ms | "bash mode" |
| Memory append | `KEY_PRESSED` (`key=="#"`) | system | tone 660 Hz 50 ms | "remembered" |
| Slash popup open | `INTERACTIVE_MENU_DETECTED` | narrator | tone 880 Hz 50 ms | "{selected_text}" |
| Menu move | `INTERACTIVE_MENU_UPDATED` | narrator | silence 20 ms | "{selected_text}" |
| Menu close | `INTERACTIVE_MENU_CLEARED` | system | tone 330 Hz 60 ms | — |
| Approval dialog open | `INTERACTIVE_MENU_DETECTED` (with title match) | alert | chord (440, 550, 660) 150 ms | "approval needed: {selected_text}" |
| Bash command request | predicate on approval title | alert | chord (330, 440) 150 ms | "run command? {selected_text}" |
| File edit request | predicate on approval title | alert | chord (440, 660) 150 ms | "edit file? {selected_text}" |
| Thinking started | `ANSI_CURSOR_MOVED` (`reason=="column"`) | — | silence 200 ms | — |
| Tool running | `ANSI_LINE_ERASED` (`mode==2`) | — | tone 220 Hz 40 ms (throttled) | — |
| Queued input | `ANSI_SGR_CHANGED` (added `reverse`) | system | tone 550 Hz 40 ms | "queued" |
| Network retry | `ANSI_BELL` | alert | chord (220, 330) 200 ms | "retrying" |
| Token meter tick | `ANSI_OSC_RECEIVED` (`category=="title"`) | — | silence 10 ms | — |
| Plan review start | `OUTPUT_LINE_APPENDED` (regex on "## Plan") | narrator | tone 440 Hz 80 ms | "plan" |
| Plan review apply | `INTERACTIVE_MENU_DETECTED` (title "Apply this plan?") | alert | chord (440, 660, 880) 180 ms | "apply plan?" |
| Diff view open | `ANSI_DISPLAY_CLEARED` (`mode==2`) followed by diff lines | narrator | tone 660 Hz 60 ms | "diff" |
| Tool denied | `OUTPUT_LINE_APPENDED` (regex on "denied") | alert | tone 220 Hz 120 ms | "denied" |
| Session resumed | `ANSI_DISPLAY_CLEARED` + prompt redraw | system | chord (440, 660) 120 ms | "resumed" |

All predicates above use the grammar documented in
[AUDIO.md](AUDIO.md#predicate-grammar); templates pull names straight
from each event's payload (see [EVENTS.md](EVENTS.md)).

---

## Where to go next

* [USER_MANUAL.md](USER_MANUAL.md) — five-minute tour of ASAT itself.
* [AUDIO.md](AUDIO.md) — how to write bindings against the matrix
  above.
* [EVENTS.md](EVENTS.md) — payload schemas for every event type
  referenced here.
