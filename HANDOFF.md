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

Currently **764 passing**.

## Suggested next PR

**F22** — `--log path.jsonl` diagnostic file. A single
`JsonlEventLogger` subscriber that writes every `Event` it sees to a
newline-delimited JSON file (one event per line), wired up through a
new `--log PATH` CLI flag. The file rotates on every session so a
long-running ASAT does not grow an unbounded log. Tests: fixture that
pipes a scripted sequence of events through the logger and asserts
the file round-trips. Doc: a short "Replaying a log" entry in the
user manual plus a payload reference in `EVENTS.md`. Unlocks remote
debugging of audio issues — the user can attach a log to an issue and
a maintainer can replay it locally into a test harness.

Lighter alternatives if F22 feels heavy: **F4** command history
(ring buffer + Up/Down in INPUT mode); **F23** Tab completion of
executables + paths; **F34** completion alert when focus has moved;
**F3** bank reload (a `:reload-bank` meta-command plus a file-mtime
watcher so hand-edits to the saved JSON reload live without
quitting); **F39a** read-only event log viewer (first slice of the
trigger → jump → edit loop described in F39); **F41** first-run
silent-sink guard (small CLI UX polish); **F48** discoverability
(`:reset` row + SETTINGS HELP_LINES). Hygiene palate cleanser: **F49**
pick any one bullet.

Every open feature has a full entry in
[`docs/FEATURE_REQUESTS.md`](docs/FEATURE_REQUESTS.md) with gap,
sketch, and pointers to the code and docs the implementation will
touch.
