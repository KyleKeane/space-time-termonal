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
    EventType.COMMAND_COMPLETED_AWAY,
    EventType.COMMAND_FAILED,
    EventType.COMMAND_FAILED_STDERR_TAIL,
    EventType.COMMAND_CANCELLED,
    EventType.COMMAND_QUEUED,
    EventType.QUEUE_DRAINED,
    EventType.OUTPUT_CHUNK,
    EventType.ERROR_CHUNK,
    EventType.OUTPUT_STREAM_PAUSED,
    EventType.OUTPUT_STREAM_BEAT,
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
    EventType.SETTINGS_SEARCH_OPENED,
    EventType.SETTINGS_SEARCH_UPDATED,
    EventType.SETTINGS_SEARCH_CLOSED,
    EventType.SETTINGS_RESET_OPENED,
    EventType.SETTINGS_RESET_CLOSED,
    EventType.HELP_REQUESTED,
    EventType.PROMPT_REFRESH,
    EventType.FIRST_RUN_DETECTED,
    EventType.FIRST_RUN_TOUR_STEP,
    EventType.WORKSPACE_OPENED,
    EventType.NOTEBOOK_OPENED,
    EventType.NOTEBOOK_CREATED,
    EventType.NOTEBOOK_LISTED,
    EventType.BOOKMARK_CREATED,
    EventType.BOOKMARK_JUMPED,
    EventType.BOOKMARK_REMOVED,
    EventType.VERBOSITY_CHANGED,
    EventType.BANK_RELOADED,
    EventType.ANSI_OSC_RECEIVED,
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
        # F34: louder, wider chime for "your command finished while you
        # were away". A hard right placement keeps it unmistakable
        # against any narration the user happens to be listening to.
        SoundRecipe(
            id="alert_away",
            kind="chord",
            params={"frequencies": [440.0, 659.25, 880.0], "duration": 0.3},
            volume=0.95,
            azimuth=55.0,
            elevation=10.0,
        ),
        # F37: pacing cues for long-running commands. `stream_paused`
        # is a single low sine so the user hears the stream go quiet
        # without being startled. `stream_beat` is a very short, very
        # quiet blip so a noisy build feels like it's ticking along
        # without the progress tick itself becoming annoying.
        SoundRecipe(
            id="stream_paused",
            kind="tone",
            params={"frequency": 196.0, "duration": 0.12, "waveform": "sine"},
            volume=0.45,
            elevation=-10.0,
        ),
        SoundRecipe(
            id="stream_beat",
            kind="tone",
            params={"frequency": 1480.0, "duration": 0.02, "waveform": "sine"},
            volume=0.25,
            elevation=25.0,
        ),
        # F7: short, unobtrusive blip for OSC 133 prompt-start markers.
        # Quieter and higher than `start` so a user whose shell paints
        # a prompt every command doesn't get pummelled with the same
        # tone they hear when the kernel begins running their code.
        SoundRecipe(
            id="prompt_ready",
            kind="tone",
            params={"frequency": 990.0, "duration": 0.03, "waveform": "sine"},
            volume=0.35,
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

        # Workspace lifecycle (F50): a workspace open is overhead
        # ("system" voice) and names the project so the user is
        # oriented from the first second. Notebook open / create
        # name the notebook; list narrates the count and lets the
        # user follow up with `:open-notebook` once that ships.
        EventBinding(
            id="workspace_opened",
            event_type=EventType.WORKSPACE_OPENED.value,
            voice_id="system",
            sound_id="session_chime",
            say_template="workspace {name}, {notebook_count} notebooks",
            priority=210,
        ),
        EventBinding(
            id="notebook_opened",
            event_type=EventType.NOTEBOOK_OPENED.value,
            voice_id="system",
            say_template="notebook {name}",
            priority=200,
        ),
        EventBinding(
            id="notebook_created",
            event_type=EventType.NOTEBOOK_CREATED.value,
            voice_id="system",
            say_template="created notebook {name}",
            priority=180,
        ),
        EventBinding(
            id="notebook_listed",
            event_type=EventType.NOTEBOOK_LISTED.value,
            voice_id="narrator",
            say_template="{summary}",
            priority=140,
        ),

        # Cell bookmarks (F35).
        EventBinding(
            id="bookmark_created",
            event_type=EventType.BOOKMARK_CREATED.value,
            voice_id="system",
            sound_id="soft_tick",
            say_template="bookmarked {name}",
            priority=170,
        ),
        EventBinding(
            id="bookmark_jumped",
            event_type=EventType.BOOKMARK_JUMPED.value,
            voice_id="system",
            sound_id="nav_blip",
            say_template="jumped to {name}",
            priority=175,
        ),
        EventBinding(
            id="bookmark_removed",
            event_type=EventType.BOOKMARK_REMOVED.value,
            voice_id="system",
            sound_id="cancel",
            say_template="removed bookmark {name}",
            priority=160,
        ),

        # Narration verbosity presets (F31). Kept at the "minimal"
        # tier so the user hears every preset change, including when
        # they just dropped the bank to minimal.
        EventBinding(
            id="verbosity_changed",
            event_type=EventType.VERBOSITY_CHANGED.value,
            voice_id="system",
            sound_id="tick",
            say_template="verbosity {level}",
            priority=180,
            verbosity="minimal",
        ),

        # F3 `:reload-bank`. Kept at the "minimal" tier because a
        # reload always happens in response to the user typing an
        # explicit meta-command, and a silent success would leave
        # them wondering whether the file parsed.
        EventBinding(
            id="bank_reloaded",
            event_type=EventType.BANK_RELOADED.value,
            voice_id="system",
            sound_id="soft_tick",
            say_template="bank reloaded from disk",
            priority=175,
            verbosity="minimal",
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
        # F36: auto-read the last stderr lines after the failure chord.
        # Disable this binding (enabled=false) to silence the tail and
        # keep only the minimal failure cue.
        EventBinding(
            id="command_failed_stderr_tail",
            event_type=EventType.COMMAND_FAILED_STDERR_TAIL.value,
            voice_id="alert",
            say_template="{tail_text}",
            priority=180,
        ),
        EventBinding(
            id="command_cancelled",
            event_type=EventType.COMMAND_CANCELLED.value,
            voice_id="system",
            sound_id="cancel",
            say_template="cancelled",
            priority=150,
        ),
        # F62: queue lifecycle. COMMAND_QUEUED fires on every
        # submission when the execution worker is active; a small
        # tick confirms the keystroke landed even if earlier cells
        # are still running. QUEUE_DRAINED fires once the queue has
        # emptied — a soft cue the user can treat as "ready for the
        # next batch".
        EventBinding(
            id="command_queued",
            event_type=EventType.COMMAND_QUEUED.value,
            sound_id="soft_tick",
            priority=90,
        ),
        EventBinding(
            id="queue_drained",
            event_type=EventType.QUEUE_DRAINED.value,
            sound_id="soft_tick",
            priority=80,
        ),
        # F34: distinctive follow-up chime when focus moved away from
        # the cell while it was running. The normal completion binding
        # has already fired for correctness; this is the "come back"
        # nudge. Silence it with enabled=false.
        EventBinding(
            id="command_completed_away",
            event_type=EventType.COMMAND_COMPLETED_AWAY.value,
            voice_id="alert",
            sound_id="alert_away",
            say_template="completed in background",
            priority=225,
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

        # F37: pacing cues for long-running commands. `stream_paused`
        # fires once per quiet window (default: five seconds with no
        # chunk); `stream_beat` fires every thirty seconds the stream
        # is alive. Kept at the default "normal" tier so they play out
        # of the box — minimal banks skip them, and users who want
        # total silence during long builds disable the bindings.
        EventBinding(
            id="output_stream_paused",
            event_type=EventType.OUTPUT_STREAM_PAUSED.value,
            sound_id="stream_paused",
            priority=85,
        ),
        EventBinding(
            id="output_stream_beat",
            event_type=EventType.OUTPUT_STREAM_BEAT.value,
            sound_id="stream_beat",
            priority=70,
        ),

        # Navigation cues: mode changes are overhead ("system") and
        # name the new mode; cell changes play the `nav_blip` and read
        # the focused cell's command so the user knows what they are
        # standing on. Buffer-only transitions are suppressed upstream
        # (see NotebookCursor._transition) so these bindings do not
        # fire per keystroke.
        EventBinding(
            id="focus_changed_mode",
            event_type=EventType.FOCUS_CHANGED.value,
            voice_id="system",
            sound_id="focus_shift",
            say_template="{new_mode}",
            predicate="transition == mode",
            priority=110,
        ),
        EventBinding(
            id="focus_changed_heading",
            event_type=EventType.FOCUS_CHANGED.value,
            voice_id="narrator",
            sound_id="nav_blip",
            say_template="heading level {heading_level} {heading_title}",
            predicate="transition == cell and kind == 'heading'",
            priority=120,
        ),
        EventBinding(
            id="focus_changed_cell",
            event_type=EventType.FOCUS_CHANGED.value,
            voice_id="narrator",
            sound_id="nav_blip",
            say_template="{command}",
            predicate="transition == cell and kind == 'command'",
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

        # F21b search overlay — three short cues so a blind user hears
        # the composer open, the match count change on each keystroke,
        # and the composer close. The record-level jump that follows a
        # match is narrated by the existing `settings_focused` binding.
        EventBinding(
            id="settings_search_opened",
            event_type=EventType.SETTINGS_SEARCH_OPENED.value,
            voice_id="system",
            sound_id="nav_blip",
            say_template="search",
            priority=200,
        ),
        EventBinding(
            id="settings_search_updated",
            event_type=EventType.SETTINGS_SEARCH_UPDATED.value,
            voice_id="system",
            say_template="{match_count} matches",
            priority=150,
        ),
        EventBinding(
            id="settings_search_closed",
            event_type=EventType.SETTINGS_SEARCH_CLOSED.value,
            voice_id="system",
            sound_id="soft_tick",
            say_template="search closed",
            priority=150,
        ),

        # F21c reset overlay — confirmation prompt narrates the scope
        # and how many records would change, so the user can back out
        # before they lose any in-progress tweaks. Closing narrates
        # three distinct outcomes (applied, already-at-defaults, or
        # cancelled) so the audible feedback matches what actually
        # happened.
        EventBinding(
            id="settings_reset_opened",
            event_type=EventType.SETTINGS_RESET_OPENED.value,
            voice_id="alert",
            sound_id="settings_chime",
            say_template=(
                "reset {scope}? press enter to confirm, escape to cancel"
            ),
            priority=230,
        ),
        EventBinding(
            id="settings_reset_applied",
            event_type=EventType.SETTINGS_RESET_CLOSED.value,
            voice_id="system",
            sound_id="settings_save",
            say_template="reset {scope} to defaults",
            predicate="outcome == applied",
            priority=220,
        ),
        EventBinding(
            id="settings_reset_already_default",
            event_type=EventType.SETTINGS_RESET_CLOSED.value,
            voice_id="system",
            sound_id="soft_tick",
            say_template="{scope} already at defaults",
            predicate="outcome == already_default",
            priority=215,
        ),
        EventBinding(
            id="settings_reset_cancelled",
            event_type=EventType.SETTINGS_RESET_CLOSED.value,
            voice_id="system",
            sound_id="soft_tick",
            say_template="reset cancelled",
            predicate="outcome == cancelled",
            priority=210,
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

        # First-run onboarding: a returning user never hears this.
        # The TerminalRenderer prints each welcome line; the narrator
        # voice speaks a short greeting so the audio-only experience
        # still acknowledges the tour. High priority so it wins over
        # whatever else is queued during startup.
        EventBinding(
            id="first_run_welcome",
            event_type=EventType.FIRST_RUN_DETECTED.value,
            voice_id="narrator",
            sound_id="settings_chime",
            say_template=(
                "Welcome to ASAT. Type colon help to hear the keystroke "
                "cheat sheet."
            ),
            priority=250,
        ),

        # F43: the guided first-command tour. The notebook's first cell
        # has just been pre-populated with `echo hello, ASAT`; this
        # narrator beat tells the user they can press Enter to run it
        # or Escape to clear and type their own command. Fires once,
        # right after the F20 welcome.
        EventBinding(
            id="first_run_tour_step",
            event_type=EventType.FIRST_RUN_TOUR_STEP.value,
            voice_id="narrator",
            say_template=(
                "Your first cell has {command} ready to go. "
                "Press Enter to run it."
            ),
            priority=245,
        ),

        # Prompt context: when the user lands in INPUT mode AFTER a
        # command has finished, narrate the trailing exit code so a
        # blind user has an immediate auditory cue for "that failed".
        # Success (exit 0) is intentionally silent — the completion
        # chord already marked it, and this would double the audio.
        EventBinding(
            id="prompt_refresh_failure",
            event_type=EventType.PROMPT_REFRESH.value,
            voice_id="system",
            say_template="last exit {last_exit_code}",
            predicate="last_exit_code != 0",
            priority=90,
        ),

        # F7: OSC 133 semantic prompt markers. Shells emitting these
        # (zsh+powerlevel10k, starship, kitty, vscode shell-integration,
        # etc.) signal where the prompt sits, when it ends, and when
        # the command's output begins. We surface only the prompt-start
        # cue by default — that's the moment a blind user most cares
        # about (the shell is ready for input). The other subcommands
        # remain silent until users opt in via the editor; otherwise
        # every command would emit four blips. Other ANSI_OSC_RECEIVED
        # categories (title, hyperlink, color, prompt subcommands beyond
        # A) stay silent because no predicate matches them.
        EventBinding(
            id="osc_prompt_ready",
            event_type=EventType.ANSI_OSC_RECEIVED.value,
            voice_id="system",
            sound_id="prompt_ready",
            predicate="category == prompt_start",
            priority=80,
        ),
    )
