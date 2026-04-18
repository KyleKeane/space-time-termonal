# Feature requests

Open gaps in ASAT — things the codebase does not yet do but should, in
roughly the next generation. Each entry explains what is missing, where
the gap surfaces today, and a sketch of what the fix would look like.

This file is descriptive, not prescriptive: priorities and scheduling
live in the PR queue.

---

## F1 — Cancel a running command

**Gap.** There is no INPUT-mode keybinding to cancel a command that
is currently running. The `COMMAND_CANCELLED` event type exists and
the default bank binds a cue to it, but nothing publishes it today.

**Where it surfaces.**
[USER_MANUAL.md](USER_MANUAL.md) explicitly notes: "Cancelling a
running command is a future keyboard binding; for now let commands
finish or send `:quit` to bail out of the whole session."

**Sketch.** Bind `Ctrl+C` in INPUT mode to a `cancel_command`
action. `ExecutionKernel` already owns the subprocess; add a
`cancel(cell_id)` method that terminates it and publishes
`COMMAND_CANCELLED`.

---

## F2 — Settings editor: create / delete records

**Gap.** `SettingsEditor` can edit fields on existing `Voice`,
`SoundRecipe`, and `EventBinding` records, but has no "add new" or
"delete" operations. Users who want a new record today have to edit
the bank JSON on disk and restart.

**Where it surfaces.**
[USER_MANUAL.md](USER_MANUAL.md) troubleshooting — "I want a brand-new
sound" — has to route users out to a file editor.

**Sketch.** Add `create_record(section)` and `delete_record()` to
`SettingsEditor`, surface them as key actions in `SettingsController`
(e.g. `a` = add, `d` = delete, with a confirmation step on delete).
Defaults for a fresh record are obvious (`Voice()` neutral,
`SoundRecipe(kind="silence")`, `EventBinding()` disabled).

---

## F3 — Reload bank from disk

**Gap.** Ctrl+S writes the in-memory bank to disk; there is no inverse
that discards uncommitted edits and reloads the last saved bank.

**Where it surfaces.** A user experimenting with settings can only
undo by remembering every edit they made, or by closing the editor
and reopening it (which does not reload from disk — the session still
holds the live bank).

**Sketch.** Add `SettingsEditor.reload(path)` that calls
`SoundBank.load(path)` and swaps the active bank after a confirmation
prompt. Bind to a keystroke (Ctrl+R) in SETTINGS mode.

---

## F4 — Command history

**Gap.** Up / Down in INPUT mode do nothing. There is no way to recall
or search past commands.

**Where it surfaces.** Every real terminal has this; its absence is
felt the first time a user wants to repeat the previous command.

**Sketch.** Extend `Session` with an ordered list of commands
submitted this session. Bind Up / Down in INPUT mode to walk it;
Ctrl+R opens reverse-incremental search (a small overlay state
machine mirroring the approach `SettingsEditor` already uses).

---

## F5 — Windows-native TTS adapter

**Gap.** The shipping `ToneTTSEngine` is a parametric tone generator
— useful as a self-contained default but not a real voice. On Windows
the obvious target is SAPI / OneCore TTS via `pywin32` (or `ctypes`
to avoid adding a dep).

**Where it surfaces.** [ARCHITECTURE.md](ARCHITECTURE.md) phase
history lists this as a next-generation item.

**Sketch.** Implement the `TTSEngine` protocol against SAPI,
expose a `SapiTTSEngine` class, let the user pick it via the `engine`
field on `Voice`. Add a Voice-level routing step in `SoundEngine`
that picks the right engine by id. Keep `ToneTTSEngine` as the
deterministic fallback for headless tests.

---

## F6 — Live-speaker audio sink

**Gap.** `AudioSink` ships with `MemorySink` (accumulates buffers for
tests) and `WavFileSink` (writes to disk). Neither plays audio on
actual speakers, so the terminal is silent end-to-end until a real
sink exists.

**Where it surfaces.** [ARCHITECTURE.md](ARCHITECTURE.md) phase
history calls this out as future work.

**Sketch.** Wrap `winsound` (stdlib) or `sounddevice` for low-latency
playback; implement the `AudioSink` protocol. Buffer management and
sample-rate coercion already live in `AudioBuffer`.

---

## F7 — Default binding for OSC 133 semantic prompt

**Gap.** `ANSI_OSC_RECEIVED` is emitted and
[CLAUDE_CODE_MODES.md](CLAUDE_CODE_MODES.md) points at OSC 133 as a
prompt-boundary signal, but the default bank has no OSC 133 binding.

**Where it surfaces.** Users running shells that emit OSC 133 (zsh
with powerlevel, starship, etc.) get a useful stream of prompt /
command / output markers but hear nothing unless they add a binding.

**Sketch.** Add a predicate-gated `ANSI_OSC_RECEIVED` binding in
`asat/default_bank.py` that matches `body` starting with `133;` and
routes to `system` voice with a short tone on prompt-ready. Test it
with a recorded fixture.

---

## F8 — Measured HRTF loader for end users

**Gap.** `HRTFProfile.from_stereo_wav` exists and the `convolve`
fast-path correctly handles dense kernels, but there is no user
surface for swapping in a measured HRTF. Today it requires editing
Python code.

**Where it surfaces.** [AUDIO.md](AUDIO.md) mentions the loader but
does not document a user path to it.

**Sketch.** Add an `hrtf_path` field to the top-level `SoundBank`
(optional; null → synthetic). `SoundEngine` loads the measured
profile at `set_bank(...)` and uses it for every spatialised cue.
Document the SOFA-to-stereo-WAV conversion recipe in AUDIO.md.

---

## F9 — Root README.md

**Gap.** The repo root has no README.md — anyone landing on GitHub
sees a directory listing with no entry point.

**Where it surfaces.** First-contact experience for contributors and
users alike.

**Sketch.** Short landing page (~30 lines) with: one-paragraph
description, install / run snippet, pointer into `docs/`.
Status: addressed by the same PR that ships this file.

---

## How to add an entry

Append a section using the template:

```
## F<N> — <short name>

**Gap.** <what the code does not do today>

**Where it surfaces.** <user-visible consequence or doc citation>

**Sketch.** <rough implementation approach>
```

Keep entries small and user-facing. Cross-cutting infrastructure
changes (testing, CI) belong in a separate log.
