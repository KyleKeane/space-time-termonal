# Audio reference

ASAT is a self-voicing terminal. Everything a user hears — narration,
cues, spatial placement, exit-code chimes, menu motion — is produced
by one pipeline:

```
Event ──▶ SoundBank bindings ──▶ SoundEngine ──▶ TTS + SoundGenerator
                                       │
                                       ▼
                                  Spatializer (HRTF)
                                       │
                                       ▼
                                   AudioSink
```

The bank is pure data. The engine is pure dispatch. The generators
synthesise waveforms from parameters. The spatializer places each
buffer in 3D by azimuth / elevation. Nothing is hard-coded: swap in a
different `SoundBank` and the terminal starts reacting to a different
set of events the instant `SoundEngine.set_bank(...)` returns.

This page is the reference for that pipeline. For the event vocabulary
see [EVENTS.md](EVENTS.md); for the overall module map see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Data model

Everything persistable lives in `asat/sound_bank.py`. All four records
are frozen dataclasses; the whole bank round-trips through JSON and
matches `asat/sound_bank_schema.json`.

### `Voice`

A parametric TTS configuration. One `Voice` can power many bindings;
per-binding `voice_overrides` lets each binding nudge pitch or azimuth
without duplicating the record.

| Field       | Type    | Default | Notes                                             |
|-------------|---------|---------|---------------------------------------------------|
| `id`        | str     | —       | Stable handle other records reference.            |
| `engine`    | str     | `""`    | TTS backend id (empty = built-in `ToneTTSEngine`).|
| `rate`      | float   | `1.0`   | Multiplier around 1.0 (speaking speed).           |
| `pitch`     | float   | `1.0`   | Multiplier around 1.0.                            |
| `volume`    | float   | `1.0`   | Linear gain, `0.0` = silent.                      |
| `azimuth`   | degrees | `0.0`   | `-180..180`, `0` = ahead, `+90` = right ear.      |
| `elevation` | degrees | `0.0`   | `-90..90`, `0` = eye level, `+90` = overhead.     |
| `metadata`  | dict    | `{}`    | Free-form, opaque to the engine.                  |

### `SoundRecipe`

A parametric non-speech cue. `kind` picks which generator consumes it;
`params` holds kind-specific fields. Per-binding `sound_overrides`
can nudge `volume` / `azimuth` / `elevation` at render time.

| Kind      | Required params                                  | Optional params                                       |
|-----------|--------------------------------------------------|-------------------------------------------------------|
| `tone`    | `frequency` (Hz), `duration` (s)                 | `waveform` (sine/square/triangle/sawtooth), `attack`, `release`, `harmonics` |
| `chord`   | `frequencies` (list[Hz]), `duration`             | `waveform`, `attack`, `release`, `spread`             |
| `sample`  | `path` (str)                                     | `loop`, `start`, `end`                                |
| `silence` | `duration`                                       | —                                                     |

All recipes share `volume`, `azimuth`, `elevation`.

### `EventBinding`

Glue: given an `EventType`, optionally pick a voice + sound recipe,
decorate with a narration template, gate with a predicate, order
against siblings with `priority`.

| Field             | Type                 | Notes                                                                    |
|-------------------|----------------------|--------------------------------------------------------------------------|
| `id`              | str                  | Unique inside the bank.                                                  |
| `event_type`      | str                  | Matches an `EventType.value` (e.g. `"cell.created"`).                    |
| `voice_id`        | str \| null          | Reference to a `Voice.id` in the same bank.                              |
| `sound_id`        | str \| null          | Reference to a `SoundRecipe.id`.                                         |
| `say_template`    | str                  | `str.format_map` template rendered against the event payload.            |
| `predicate`       | str                  | See [predicate grammar](#predicate-grammar). Empty = always match.       |
| `priority`        | int                  | Higher runs first. Default `100`.                                        |
| `enabled`         | bool                 | `False` silences without deleting.                                       |
| `voice_overrides` | `dict[str, float]`   | Per-binding overrides: `rate`, `pitch`, `volume`, `azimuth`, `elevation`.|
| `sound_overrides` | `dict[str, float]`   | Per-binding overrides: `volume`, `azimuth`, `elevation`.                 |

At least one of `voice_id`, `sound_id`, or a non-empty `say_template`
must be set.

### `SoundBank`

Immutable tuple of `voices`, `sounds`, `bindings`, plus a
`version`. `validate()` checks unique ids per kind and that every
binding points at an existing voice / sound. `load(path)` /
`save(path)` are thin JSON wrappers; use them to persist user edits.

---

## Predicate grammar

Evaluated by `DefaultPredicateEvaluator` in `asat/sound_engine.py`.
Three forms, line-level so the grammar stays diff-friendly:

```
<empty>               always match
key == <literal>      equality (literal via ast.literal_eval)
key != <literal>      inequality
key in [<literals>]   membership; RHS must be a list / tuple / set
```

Keys are looked up on the event payload with `dict.get(...)`; absent
keys compare as `None`. Literals are parsed with `ast.literal_eval`
so `0`, `"text"`, `True`, `None`, and lists all work. Unknown
operators raise `SoundEngineError` at evaluation time rather than
silently passing — typos surface immediately.

Examples:

```
exit_code == 0
stream == "stderr"
action in ["copy_line", "copy_all"]
timed_out != False
transition == mode        # FOCUS_CHANGED: only when the focus mode changed
transition == cell        # FOCUS_CHANGED: only when the focused cell changed
```

String RHS values may be quoted (`"stderr"`) or bare (`mode`) — both
are accepted because `_parse_literal` falls back to the raw string
when `ast.literal_eval` can't parse it.

Plug in a different evaluator by passing `predicate=...` to
`SoundEngine(...)`; the `PredicateEvaluator` protocol is tiny
(`matches(expression, payload) -> bool`).

---

## Template grammar

`say_template` uses Python `str.format_map` with a default-to-empty-
string missing-key dict. Every payload field listed in
[EVENTS.md](EVENTS.md) is available as a placeholder. Missing keys
render as empty string rather than raising, so a template can
reference optional fields without guarding.

Examples:

```
"cell {cell_id} created"
"command {command} exited with {exit_code}"
"{line}"                # stream whatever text the payload carries
""                      # no narration — cue-only binding
```

If `say_template` is empty or renders to only whitespace, the binding
still plays its `sound_id` (if any) but skips the TTS path.

---

## Synthesis: SoundGenerator registry

`asat/sound_generators.py` turns `SoundRecipe` records into mono
`AudioBuffer` waveforms. One generator per `SOUND_KINDS` entry, all
plugged into `SoundGeneratorRegistry`:

| Kind      | Generator           | Synthesis                                                                                        |
|-----------|---------------------|--------------------------------------------------------------------------------------------------|
| `tone`    | `ToneGenerator`     | sine / square / triangle / sawtooth; linear attack-release envelope; optional harmonics.         |
| `chord`   | `ChordGenerator`    | sum of tones, normalised by partial count so the peak stays under unity.                         |
| `sample`  | `SampleGenerator`   | loads `.wav`, downmixes stereo by averaging, linearly resamples, trims, optionally loops.        |
| `silence` | `SilenceGenerator`  | a zero-filled buffer of the requested duration. Useful as an explicit gap.                       |

The registry is pure stdlib (`math`, `wave`) and is the only layer
that touches WAV I/O. Plug in a custom generator by constructing your
own registry (`SoundGeneratorRegistry(...)`) and passing it to
`SoundEngine(generators=...)`.

---

## Dispatch: SoundEngine

`asat/sound_engine.py` subscribes to every `EventType` that the
active bank's enabled bindings mention. Per event:

1. Skip if `event.source == SOURCE_NAME` (prevents feedback loops:
   `AUDIO_SPOKEN` is itself an event, and a binding that matched it
   would loop forever).
2. Look up every binding for `event.event_type`, sorted by
   `priority` descending.
3. For each: evaluate the `predicate`; if it matches, render.
4. Render = resolve `voice` + `sound`, apply per-binding overrides
   via `dataclasses.replace`, run the `say_template`, synthesise
   speech (if voice + non-empty text) and sound (if a recipe is
   attached), spatialise each by its resolved position, mix, clamp,
   play on the `AudioSink`.
5. Publish `AUDIO_SPOKEN` with `event_type`, `binding_id`, rendered
   `text`, `voice_id`, `sound_id`.

`set_bank(bank)` re-subscribes the engine to exactly the event types
the new bank wants — old subscriptions are dropped, new ones added.
`close()` drops every subscription and closes the underlying sink.

### Per-binding overrides

`voice_overrides` and `sound_overrides` let a single `Voice` or
`SoundRecipe` power many distinct events with small variations. The
engine applies them via `dataclasses.replace` **before** rendering:

```python
EventBinding(
    id="error_line",
    event_type="error.chunk",
    voice_id="alert",
    say_template="{line}",
    voice_overrides={"pitch": 0.7, "azimuth": 60.0},
)
```

This plays the `alert` voice at a lower pitch and further to the
right whenever an error line flies past, without cloning the
underlying `alert` record. Allowed override keys are restricted to
the numeric fields (`rate`, `pitch`, `volume`, `azimuth`,
`elevation` for voices; `volume`, `azimuth`, `elevation` for sounds)
so a typo fails loudly at load time.

---

## Spatialisation: HRTF

`asat/hrtf.py` holds the head-related transfer function pipeline. The
`Spatializer` convolves a mono buffer with a left / right IR pair
derived from a `SpatialPosition(azimuth, elevation)`. Two sources at
different azimuths arrive at different inter-aural times and gains,
which is enough for the ear to hear "left of centre" vs. "right of
centre" even through cheap headphones.

Two ways to obtain a profile:

* `HRTFProfile.synthetic(position)` — builds a sparse impulse-response
  pair (one nonzero tap per ear) modelling only the inter-aural time
  and level difference. This is the shipping default. It is not a
  substitute for a measured HRTF but is enough for front-vs-side and
  left-vs-right discrimination on headphones.
* `HRTFProfile.from_stereo_wav(path)` — loads a stereo WAV whose two
  channels are the left-ear and right-ear impulse responses. This is
  the shape most SOFA-derived HRIR datasets land in after conversion.

`convolve(signal, kernel)` branches on kernel shape:

1. Sparse impulse (one nonzero tap) — delay-and-scale in O(n). Every
   synthetic profile takes this path; no third-party import needed.
2. All-zero kernel — returns a zero buffer of length `n + m − 1`.
3. Dense kernel — `numpy.convolve` when `numpy` is importable, else a
   pure-Python fallback. Measured HRTFs (128–512 taps) benefit from
   the numpy path by ~1000× on long narration buffers; without numpy
   the fallback still produces correct audio, only slower.

numpy is therefore an optional accelerator only — `pip install numpy`
is never a requirement for ASAT to run.

Conventions used by every module:

* `azimuth`: `-180..180` degrees, `0` = straight ahead, `+90` =
  directly to the right, `-90` = directly to the left.
* `elevation`: `-90..90` degrees, `0` = eye level, `+90` = overhead,
  `-90` = below.

The default bank uses this to encode meaning by direction: left for
ordinary output, right for errors, overhead for meta-events. Users
can reshape it through the settings editor without rewriting code.

---

## Default bank

`asat/default_bank.py::default_sound_bank()` returns the SoundBank a
fresh install ships with. It is deliberately conservative:

**Three voices** (`narrator` left, `alert` right, `system` overhead)
cover almost every binding. The azimuth layout is the main navigation
aid: output comes from one side, errors from the other, and
meta-events float above.

**Sound cues** are short (all under ~250 ms) and percussive so long
sessions stay calm: `tick`, `soft_tick`, `submit`, `start`,
`success_chord` (major triad), `failure_chord` (low minor-second),
`cancel` (low square), `session_chime`, `nav_blip`, `menu_open` /
`menu_close` (inverted chord pair), `clipboard`, `focus_shift`,
`tui_menu_alert`, `settings_chime`, `settings_save`.

**Predicate-gated branches:**

* `COMMAND_COMPLETED` splits on `exit_code == 0` vs. `exit_code != 0`
  so a plain success is a chord, but a nonzero exit gets the failure
  chord and an `alert`-voice readout.
* `COMMAND_FAILED` splits on `timed_out == True` vs.
  `timed_out == False` so a timeout sounds distinct from a generic
  crash.

**Intentionally silent events** — listed in `COVERED_EVENT_TYPES`'s
complement in the code and enforced by a test — are `KEY_PRESSED`,
`ACTION_INVOKED`, `OUTPUT_LINE_APPENDED`, `SCREEN_UPDATED`, and every
`ANSI_*` low-level event. They're kept quiet by default so the stock
experience isn't chatty; users opt in by adding a binding in the
settings editor.

The invariant `every COVERED_EVENT_TYPES entry has at least one
binding AND every omission is explicitly in the allow-list` is
covered by `tests/test_default_bank.py`. Adding a new `EventType`
either puts it in `COVERED_EVENT_TYPES` (with a binding) or extends
the allow-list — never silently drops through.

---

## Live editing: SettingsEditor

`asat/settings_editor.py` is a keyboard-driven, headless editor that
walks a three-level state machine over the bank:

```
SECTION   ["voices", "sounds", "bindings"]
   ↓
RECORD    list of Voice / SoundRecipe / EventBinding
   ↓
FIELD     typed fields on the current record
```

Navigation: `next` / `prev` wrap at each level; `enter` descends;
`back` ascends. `edit(raw)` parses the raw string against the
focused field's declared type and refuses the mutation on parse
error without disturbing editor state.

Per-field parsers (complete list in `asat/settings_editor.py`):

| Field                                  | Parser                                           |
|----------------------------------------|--------------------------------------------------|
| `rate`, `pitch`, `volume`, `azimuth`, `elevation` | float                                |
| `priority`                             | int                                              |
| `enabled`                              | bool (`true`/`false`/`yes`/`no`/`1`/`0`/`on`/`off`)|
| `voice_id`, `sound_id`                 | optional string; `null` / `none` / empty clears  |
| `id`, `event_type`, `say_template`, `predicate`, `engine` | raw string                    |
| `kind`                                 | string restricted to `SOUND_KINDS`               |
| `params`                               | JSON object                                      |
| `voice_overrides`, `sound_overrides`   | JSON object with allow-listed numeric keys       |

Every successful edit produces a new immutable `SoundBank` via
`dataclasses.replace` and revalidates referential integrity; a broken
edit rolls back transparently. The editor is self-voicing by default:
`SETTINGS_OPENED`, `SETTINGS_CLOSED`, `SETTINGS_FOCUSED`,
`SETTINGS_VALUE_EDITED`, `SETTINGS_SAVED` are wired into the default
bank so the engine narrates navigation and mutations without extra
glue code.

The editor is driven in the live TUI by `SettingsController`
(`asat/settings_controller.py`), which owns the open/close lifecycle
and an edit sub-mode (`begin_edit` / `commit_edit` / `cancel_edit`)
so keystrokes compose the replacement value in place. The
`InputRouter` (`asat/input_router.py`) binds the controller to the
`SETTINGS` focus mode:

* **UP** / **DOWN** — prev / next
* **LEFT** / **ESC** — ascend
* **RIGHT** / **ENTER** — descend
* **e** — begin edit (at FIELD level)
* **Ctrl+S** — save
* **Ctrl+Q** — close

Users enter the editor either from `NOTEBOOK` mode via **Ctrl+,** or
from any mode by typing `:settings` at the command line (the meta-
command is intercepted by `_submit` before the kernel ever sees it).

---

## Extending the audio stack

| Want to…                                      | Do this                                                                           |
|-----------------------------------------------|-----------------------------------------------------------------------------------|
| Add an event                                  | Add to `EventType`, document payload in [EVENTS.md](EVENTS.md), add a binding.    |
| Ship a new stock sound                        | Extend `_default_sounds()` + the binding in `_default_bindings()`, update tests.  |
| Try a different TTS backend                   | Implement the `TTSEngine` protocol, pass via `SoundEngine(tts=...)`.              |
| Swap the audio sink (live speakers, file)     | Implement `AudioSink`, pass via `SoundEngine(sink=...)`.                          |
| Add a new sound kind                          | Add to `SOUND_KINDS`, implement a generator, register in `SoundGeneratorRegistry`.|
| Change the predicate grammar                  | Implement `PredicateEvaluator`, pass via `SoundEngine(predicate=...)`.            |
| Reshape audio without code                    | Open `:settings`, edit, Ctrl+S. The engine rebinds live.                          |

Everything plug-in-shaped is a `Protocol` so custom implementations
never need to import from each other — only the protocol interface.
