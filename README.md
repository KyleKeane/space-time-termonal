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

The test suite is the current entry point; an end-user binary is on
the roadmap (see `docs/FEATURE_REQUESTS.md`).

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
