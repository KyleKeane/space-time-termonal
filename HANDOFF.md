# ASAT — handoff summary for the next Claude Code session

Paste this file as the first message of a new Claude Code session and
it will pick up without further priming.

## What ASAT is

ASAT (Accessible Spatial Audio Terminal) is a command-line terminal
**optimised for a blind user**. Every user-visible event fires a sound
cue or a short TTS narration; the keyboard is the only input; visual
chrome is deliberately minimal so a screen reader does not have to
narrate UI buttons. Pure Python 3.10+ stdlib — numpy is an optional
fast path for measured HRTFs only.

## How to work on it

Read [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) once before
your first PR. It holds the guiding principles (core Python only,
narration-first, extraordinarily simple, flat when possible, one
feature per PR, documentation lands with the code) and the PR recipe
every change follows.

The roadmap — open work and shipped history — lives in
[`docs/FEATURE_REQUESTS.md`](docs/FEATURE_REQUESTS.md). The
architecture tour lives in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The user contract
lives in [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md).

## Test command

```
python -m unittest discover -s tests -t .
```

Currently **816 passing**.

## Suggested next PR

**F4** — command history. A ring buffer keyed on the session that
remembers every submitted command; Up/Down in INPUT mode walks
backwards and forwards through it; the narrator reads the surfaced
command so the user can pick and edit. Pairs naturally with F23 (tab
completion) but stands alone as the single biggest absence for a
shell-native user. Tests: router cases for Up/Down history
traversal, a persistence case for the `Session`-attached buffer, and
a sound-engine case for the scroll cue.

Lighter alternatives if F4 feels heavy: **F23** Tab completion of
executables + paths; **F3** bank reload (a `:reload-bank` meta-command
plus a file-mtime watcher so hand-edits to the saved JSON reload live
without quitting); **F30b** Ctrl+Shift+R history overlay (F30a shipped
the single-entry replay; the browse mode is the unfinished half);
**F39a** read-only event log viewer (first slice of the
trigger → jump → edit loop described in F39); **F48** discoverability
(`:reset` row + SETTINGS HELP_LINES). Hygiene palate cleanser: **F49**
pick any one bullet.

Every open feature has a full entry in
[`docs/FEATURE_REQUESTS.md`](docs/FEATURE_REQUESTS.md) with gap,
sketch, and pointers to the code and docs the implementation will
touch.
