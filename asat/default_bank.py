"""Default SoundBank: the audio baseline every user starts with.

The function `default_sound_bank()` returns a fully-populated
`SoundBank` that covers every user-facing `EventType` with a sensible
binding. It is meant as a starting point — the in-terminal editor
(phase A5) will let users clone, tweak, and override it — but even
without any customisation the terminal should be usable by a blind
developer on day one.

Design choices baked into the default bank:

- Three narrator voices, placed in the HRTF field to encode meaning
  by direction:

      narrator   slightly left    normal stdout / status narration
      alert      slightly right   stderr and command failures
      system     straight ahead,  session- and cell-lifecycle cues
                 elevated

- Short percussive sound cues (tone, chord) for lifecycle events so
  the narrator is not triggered for every tiny mutation. Cues are
  deliberately kept under 250 ms so a long session doesn't feel
  chatty.

- Spatial separation mirrors the semantic split: left = ordinary
  program output, right = errors or trouble, overhead = navigation
  and meta-events.

- Speech templates use only payload keys that are documented in
  `docs/EVENTS.md`.

- Two noisy event types (`KEY_PRESSED`, `SCREEN_UPDATED`) are
  deliberately unbound so the default experience stays calm; users
  who want per-keystroke feedback can add bindings through the
  upcoming editor.

This module is pure data; importing it does not touch the audio
stack.
"""

from __future__ import annotations

from asat.events import EventType
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice


# Every event a user will hear by default. Events omitted here are
# intentional silences: we don't want per-keystroke or per-screen-
# refresh audio without a user asking for it.
COVERED_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.SESSION_CREATED,
    EventType.SESSION_LOADED,
    EventType.SESSION_SAVED,
    EventType.CELL_CREATED,
    EventType.CELL_UPDATED,
    EventType.CELL_REMOVED,
    EventType.CELL_MOVED,
    EventType.COMMAND_SUBMITTED,
    EventType.COMMAND_STARTED,
    EventType.COMMAND_COMPLETED,
    EventType.COMMAND_FAILED,
    EventType.COMMAND_CANCELLED,
    EventType.OUTPUT_CHUNK,
    EventType.ERROR_CHUNK,
    EventType.FOCUS_CHANGED,
    EventType.OUTPUT_LINE_FOCUSED,
    EventType.ACTION_MENU_OPENED,
    EventType.ACTION_MENU_CLOSED,
    EventType.ACTION_MENU_ITEM_FOCUSED,
    EventType.ACTION_MENU_ITEM_INVOKED,
    EventType.CLIPBOARD_COPIED,
    EventType.INTERACTIVE_MENU_DETECTED,
    EventType.INTERACTIVE_MENU_UPDATED,
    EventType.INTERACTIVE_MENU_CLEARED,
    EventType.SETTINGS_OPENED,
    EventType.SETTINGS_CLOSED,
    EventType.SETTINGS_FOCUSED,
    EventType.SETTINGS_VALUE_EDITED,
    EventType.SETTINGS_SAVED,
    EventType.HELP_REQUESTED,
})


def default_sound_bank() -> SoundBank:
    """Return the stock SoundBank. Validated on construction."""
    bank = SoundBank(
        voices=_default_voices(),
        sounds=_default_sounds(),
        bindings=_default_bindings(),
    )
    bank.validate()
    return bank


def _default_voices() -> tuple[Voice, ...]:
    """Three narrators placed left / right / overhead."""
    return (
        Voice(
            id="narrator",
            rate=1.0,
            pitch=1.0,
            volume=0.9,
            azimuth=-20.0,
            elevation=0.0,
        ),
        Voice(
            id="alert",
            rate=1.05,
            pitch=1.2,
            volume=1.0,
            azimuth=35.0,
            elevation=-5.0,
        ),
        Voice(
            id="system",
            rate=1.0,
            pitch=1.1,
            volume=0.85,
            azimuth=0.0,
            elevation=30.0,
        ),
    )


def _default_sounds() -> tuple[SoundRecipe, ...]:
    """Short, distinguishable cues that cover the lifecycle events."""
    return (
        SoundRecipe(
            id="tick",
            kind="tone",
            params={"frequency": 880.0, "duration": 0.04, "waveform": "sine"},
            volume=0.5,
        ),
        SoundRecipe(
            id="soft_tick",
            kind="tone",
            params={"frequency": 660.0, "duration": 0.03, "waveform": "sine"},
            volume=0.35,
        ),
        SoundRecipe(
            id="submit",
            kind="tone",
            params={"frequency": 520.0, "duration": 0.08, "waveform": "triangle"},
            volume=0.6,
        ),
        SoundRecipe(
            id="start",
            kind="tone",
            params={"frequency": 440.0, "duration": 0.05, "waveform": "sine"},
            volume=0.55,
        ),
        SoundRecipe(
            id="success_chord",
            kind="chord",
            params={"frequencies": [523.25, 659.25, 783.99], "duration": 0.18},
            volume=0.7,
        ),
        SoundRecipe(
            id="failure_chord",
            kind="chord",
            params={"frequencies": [130.81, 155.56], "duration": 0.22, "waveform": "sawtooth"},
            volume=0.8,
            azimuth=35.0,
        ),
        SoundRecipe(
            id="cancel",
            kind="tone",
            params={"frequency": 220.0, "duration": 0.1, "waveform": "square"},
            volume=0.55,
        ),
        SoundRecipe(
            id="session_chime",
            kind="chord",
            params={"frequencies": [392.0, 587.33, 880.0], "duration": 0.2},
            volume=0.65,
            elevation=30.0,
        ),
        SoundRecipe(
            id="nav_blip",
            kind="tone",
            params={"frequency": 1200.0, "duration": 0.025, "waveform": "sine"},
            volume=0.4,
            elevation=30.0,
        ),
        SoundRecipe(
            id="menu_open",
            kind="chord",
            params={"frequencies": [660.0, 990.0], "duration": 0.08},
            volume=0.5,
            elevation=30.0,
        ),
        SoundRecipe(
            id="menu_close",
            kind="chord",
            params={"frequencies": [990.0, 660.0], "duration": 0.08},
            volume=0.5,
            elevation=30.0,
        ),
        SoundRecipe(
            id="clipboard",
            kind="tone",
            params={"frequency": 1320.0, "duration": 0.04, "waveform": "triangle"},
            volume=0.45,
            elevation=20.0,
        ),
        SoundRecipe(
            id="focus_shift",
            kind="tone",
            params={"frequency": 780.0, "duration": 0.035, "waveform": "triangle"},
            volume=0.4,
        ),
        SoundRecipe(
            id="tui_menu_alert",
            kind="chord",
            params={"frequencies": [523.25, 783.99], "duration": 0.12},
            volume=0.55,
        ),
        SoundRecipe(
            id="settings_chime",
            kind="chord",
            params={"frequencies": [440.0, 554.37, 660.0], "duration": 0.16},
            volume=0.6,
            elevation=30.0,
        ),
        SoundRecipe(
            id="settings_save",
            kind="chord",
            params={"frequencies": [523.25, 783.99, 1046.5], "duration": 0.2},
            volume=0.7,
            elevation=20.0,
        ),
    )


def _default_bindings() -> tuple[EventBinding, ...]:
    """Map every COVERED_EVENT_TYPES entry to voices and / or sounds."""
    return (
        # Session lifecycle: spoken + a chime so the user knows the shell state.
        EventBinding(
            id="session_created",
            event_type=EventType.SESSION_CREATED.value,
            voice_id="system",
            sound_id="session_chime",
            say_template="new session",
            priority=200,
        ),
        EventBinding(
            id="session_loaded",
            event_type=EventType.SESSION_LOADED.value,
            voice_id="system",
            sound_id="session_chime",
            say_template="session loaded",
            priority=200,
        ),
        EventBinding(
            id="session_saved",
            event_type=EventType.SESSION_SAVED.value,
            voice_id="system",
            say_template="session saved",
            priority=150,
        ),

        # Cell lifecycle: small blips, no speech to stay quiet.
        EventBinding(
            id="cell_created",
            event_type=EventType.CELL_CREATED.value,
            sound_id="soft_tick",
            priority=100,
        ),
        EventBinding(
            id="cell_updated",
            event_type=EventType.CELL_UPDATED.value,
            sound_id="tick",
            priority=100,
        ),
        EventBinding(
            id="cell_removed",
            event_type=EventType.CELL_REMOVED.value,
            voice_id="system",
            sound_id="cancel",
            say_template="cell removed",
            priority=100,
        ),
        EventBinding(
            id="cell_moved",
            event_type=EventType.CELL_MOVED.value,
            sound_id="nav_blip",
            priority=100,
        ),

        # Execution kernel.
        EventBinding(
            id="command_submitted",
            event_type=EventType.COMMAND_SUBMITTED.value,
            sound_id="submit",
            priority=150,
        ),
        EventBinding(
            id="command_started",
            event_type=EventType.COMMAND_STARTED.value,
            sound_id="start",
            priority=120,
        ),
        EventBinding(
            id="command_completed_ok",
            event_type=EventType.COMMAND_COMPLETED.value,
            voice_id="system",
            sound_id="success_chord",
            say_template="completed",
            predicate="exit_code == 0",
            priority=220,
        ),
        EventBinding(
            id="command_completed_nonzero",
            event_type=EventType.COMMAND_COMPLETED.value,
            voice_id="alert",
            sound_id="failure_chord",
            say_template="completed with exit code {exit_code}",
            predicate="exit_code != 0",
            priority=215,
        ),
        EventBinding(
            id="command_failed_timeout",
            event_type=EventType.COMMAND_FAILED.value,
            voice_id="alert",
            sound_id="failure_chord",
            say_template="timed out",
            predicate="timed_out == True",
            priority=230,
        ),
        EventBinding(
            id="command_failed_generic",
            event_type=EventType.COMMAND_FAILED.value,
            voice_id="alert",
            sound_id="failure_chord",
            say_template="failed with exit code {exit_code}",
            predicate="timed_out != True",
            priority=200,
        ),
        EventBinding(
            id="command_cancelled",
            event_type=EventType.COMMAND_CANCELLED.value,
            voice_id="system",
            sound_id="cancel",
            say_template="cancelled",
            priority=150,
        ),

        # Streamed output. Speak the line so long sessions narrate progress.
        EventBinding(
            id="output_chunk",
            event_type=EventType.OUTPUT_CHUNK.value,
            voice_id="narrator",
            say_template="{line}",
            priority=80,
        ),
        EventBinding(
            id="error_chunk",
            event_type=EventType.ERROR_CHUNK.value,
            voice_id="alert",
            say_template="{line}",
            priority=90,
        ),

        # Navigation cues kept below speech priority so they are quick
        # and easy to scan past.
        EventBinding(
            id="focus_changed",
            event_type=EventType.FOCUS_CHANGED.value,
            sound_id="focus_shift",
            priority=100,
        ),
        EventBinding(
            id="output_line_focused",
            event_type=EventType.OUTPUT_LINE_FOCUSED.value,
            voice_id="narrator",
            say_template="{text}",
            priority=120,
        ),

        # Action menu — mode changes need obvious markers.
        EventBinding(
            id="action_menu_opened",
            event_type=EventType.ACTION_MENU_OPENED.value,
            sound_id="menu_open",
            priority=150,
        ),
        EventBinding(
            id="action_menu_closed",
            event_type=EventType.ACTION_MENU_CLOSED.value,
            sound_id="menu_close",
            priority=150,
        ),
        EventBinding(
            id="action_menu_item_focused",
            event_type=EventType.ACTION_MENU_ITEM_FOCUSED.value,
            voice_id="narrator",
            sound_id="nav_blip",
            say_template="{label}",
            priority=130,
        ),
        EventBinding(
            id="action_menu_item_invoked",
            event_type=EventType.ACTION_MENU_ITEM_INVOKED.value,
            voice_id="system",
            sound_id="tick",
            say_template="{label}",
            priority=160,
        ),

        # Clipboard + interactive TUI menu state.
        EventBinding(
            id="clipboard_copied",
            event_type=EventType.CLIPBOARD_COPIED.value,
            sound_id="clipboard",
            voice_id="system",
            say_template="copied {source}",
            priority=120,
        ),
        EventBinding(
            id="tui_menu_detected",
            event_type=EventType.INTERACTIVE_MENU_DETECTED.value,
            voice_id="system",
            sound_id="tui_menu_alert",
            say_template="interactive menu: {selected_text}",
            priority=180,
        ),
        EventBinding(
            id="tui_menu_updated",
            event_type=EventType.INTERACTIVE_MENU_UPDATED.value,
            voice_id="narrator",
            say_template="{selected_text}",
            priority=140,
        ),
        EventBinding(
            id="tui_menu_cleared",
            event_type=EventType.INTERACTIVE_MENU_CLEARED.value,
            sound_id="menu_close",
            priority=120,
        ),

        # Settings editor — self-voicing so the audio framework is
        # editable without any separate screen reader wiring.
        EventBinding(
            id="settings_opened",
            event_type=EventType.SETTINGS_OPENED.value,
            voice_id="system",
            sound_id="settings_chime",
            say_template="settings: {section}",
            priority=220,
        ),
        EventBinding(
            id="settings_closed",
            event_type=EventType.SETTINGS_CLOSED.value,
            voice_id="system",
            sound_id="menu_close",
            say_template="settings closed",
            priority=200,
        ),
        EventBinding(
            id="settings_focused",
            event_type=EventType.SETTINGS_FOCUSED.value,
            voice_id="narrator",
            sound_id="nav_blip",
            say_template="{section} {record_id} {field} {value}",
            priority=150,
        ),
        EventBinding(
            id="settings_value_edited",
            event_type=EventType.SETTINGS_VALUE_EDITED.value,
            voice_id="system",
            sound_id="tick",
            say_template="{field} set to {new_value}",
            priority=200,
        ),
        EventBinding(
            id="settings_saved",
            event_type=EventType.SETTINGS_SAVED.value,
            voice_id="system",
            sound_id="settings_save",
            say_template="settings saved",
            priority=220,
        ),

        # Help: speak a short summary so :help is useful without a
        # visible terminal trace. The TerminalRenderer prints the full
        # cheat sheet on the same event.
        EventBinding(
            id="help_requested",
            event_type=EventType.HELP_REQUESTED.value,
            voice_id="system",
            sound_id="settings_chime",
            say_template=(
                "help. Escape leaves any mode. Ctrl plus comma opens "
                "settings. Type colon quit to exit."
            ),
            priority=220,
        ),
    )
