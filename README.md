# ASAT — Accessible Spatial Audio Terminal

A self-voicing notebook-style terminal for blind developers on Windows.
Every state change — a keystroke, a command completion, a menu
highlight, an ANSI event — flows through one synchronous event bus
and can be bound to a spoken phrase or a spatialised tone. Keyboard
only, standard-library only, no mouse, no screen coordinates.

## Install and run

Requires Python 3.10 or newer. No runtime dependencies (`numpy` is an
optional accelerator for measured HRTFs only).

```
git clone https://github.com/KyleKeane/space-time-termonal
cd space-time-termonal
python -m unittest discover -s tests -t .
```

### Launching a session

```
python -m asat                      # interactive session, text trace on stdout
python -m asat --live               # also play audio on the speaker (Windows today)
python -m asat --wav-dir /tmp/asat  # also write every rendered buffer to WAV
python -m asat --quiet              # suppress the text trace, audio only
```

The CLI prints a short text trace as you work — a startup banner, your
keystrokes as you type, the `$ command` you submit, the captured
output, and a `[done exit=0]` line when each command finishes. That
trace exists so sighted viewers and anyone debugging can follow along;
the audio pipeline is the primary UI.

**Audio today.** `--live` uses `winsound` on Windows (stdlib, no
dependencies) and plays each rendered buffer through the speaker. On
macOS and Linux the live sink is not yet available — `--live` falls
back to the in-memory sink with a message, and `--wav-dir DIR`
captures every buffer as a numbered WAV you can review. Live POSIX
playback is tracked as [F6](docs/FEATURE_REQUESTS.md#f6--live-speaker-audio-sink).

### Five-minute tour

1. `python -m asat --live` (on Windows) or `python -m asat --wav-dir /tmp/asat`
   (elsewhere). The session-start chime plays; the trace prints
   `[asat] session <id> ready. :quit to exit.`
2. Type a command — `echo hi`, `python --version`, `git status`. Each
   keystroke narrates (audio) and echoes (text).
3. Press **Enter**. The trace prints `$ <command>`, the command runs,
   output streams, and `[done exit=0]` closes it out.
4. Press **Ctrl+N** for a new cell, **Up/Down** to walk between cells,
   **Ctrl+O** to enter OUTPUT mode and step through captured lines,
   **Ctrl+,** to open the live settings editor.
5. Type `:quit` + Enter (or Ctrl+D on POSIX / Ctrl+Z-Enter on Windows)
   to exit.

Full keystroke cheat sheet: [docs/USER_MANUAL.md](docs/USER_MANUAL.md).

## Documentation map

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layered module map,
  focus model, execution path. Read this first.
* [docs/USER_MANUAL.md](docs/USER_MANUAL.md) — keystroke cheat sheet,
  modes, troubleshooting.
* [docs/EVENTS.md](docs/EVENTS.md) — every event type and its payload.
* [docs/AUDIO.md](docs/AUDIO.md) — voices, recipes, HRTF, spatialiser.
* [docs/CLAUDE_CODE_MODES.md](docs/CLAUDE_CODE_MODES.md) — sonification
  targets for the Claude Code TUI.
* [docs/FEATURE_REQUESTS.md](docs/FEATURE_REQUESTS.md) — open gaps for
  the next generation.
