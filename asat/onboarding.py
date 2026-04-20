"""OnboardingCoordinator: a one-shot welcome tour for first-time users.

The first launch of ASAT on a fresh machine looks identical to the
hundredth. The banner says "session ready"; the audio chord plays;
nothing tells a newcomer that `:help` lists keystrokes or that
`--live` / `--wav-dir` choose where audio goes.

OnboardingCoordinator closes that gap without intruding on returning
users. It owns a sentinel file (by default `~/.asat/first-run-done`):

- If the sentinel is missing, `.run()` publishes a FIRST_RUN_DETECTED
  event carrying a short spoken tour and the path to the sentinel,
  then writes the sentinel so subsequent launches stay quiet.
- If the sentinel exists, `.run()` is a no-op and returns False.

The coordinator does no keystroke capture and does not block the
session loop — the tour is a single event the TerminalRenderer prints
and the default SoundBank narrates. Callers (CLI, tests) decide when
to invoke `.run()` and whether to suppress it entirely (e.g. with
`--quiet`).

Keeping sentinel creation *after* publishing ensures a crash mid-tour
leaves the flag unset so the user is greeted again on the next
launch — but the common case is a clean write.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, Iterable, Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType


DEFAULT_ONBOARDING_LINES: tuple[str, ...] = (
    "Welcome to the Accessible Spatial Audio Terminal.",
    "Type colon, h, e, l, p, Enter for the keystroke cheat sheet.",
    "Type colon, c, o, m, m, a, n, d, s, Enter to list every meta-command.",
    "Press Escape any time to return to notebook mode.",
    "Type colon, q, u, i, t, Enter to exit when you are done.",
)


# F43: after the welcome tour fires we pre-populate the first cell
# with a known-good command so a brand-new user hears the submit →
# start → complete → exit-code arc on their first Enter press.
FIRST_RUN_TOUR_COMMAND: str = "echo hello, ASAT"


FIRST_RUN_TOUR_LINES: tuple[str, ...] = (
    "Your first cell is ready.",
    "Press Enter to run it, or use Backspace to edit before running.",
)


# PR 4: the scripted tour seeds a three-cell demo notebook so the
# outline pane (PR 2) has something to render on first launch. Two
# heading cells + one command cell produces a two-level hierarchy
# the newcomer can scroll with `]` / `[`.
FIRST_RUN_OUTLINE_HEADINGS: tuple[tuple[int, str], ...] = (
    (1, "Welcome to ASAT"),
    (2, "Your first command"),
)


# PR 4: the third tour beat introduces the event log viewer. We
# narrate the keystroke and what pressing `t` does — enough that a
# curious user can open it themselves without us thrashing focus.
FIRST_RUN_EVENT_LOG_LINES: tuple[str, ...] = (
    "Press Control E any time to open the event log.",
    "Up and Down walk recent events; press t to replay one.",
)


# PR 4: fourth beat — announce the on-disk log file path so the user
# knows where to `tail -f`. Empty lines signal "no workspace attached,
# no file logger" so the renderer / audio bank can skip the beat.
FIRST_RUN_LOG_PATH_LINES: tuple[str, ...] = (
    "Every event is also written to a grouped text log on disk.",
)


# PR 4: final beat — tour complete; hand the user back a notebook
# they can type into. Keeps "press Enter to run" as the final cue so
# a first-time user has a next step they can't miss.
FIRST_RUN_COMPLETED_LINES: tuple[str, ...] = (
    "First-run tour complete.",
    "Press Enter to run your first command, or colon h e l p for more.",
)


SILENT_SINK_HINT = (
    "[asat] First-run welcome is narrating into an in-memory sink so "
    "you will not hear it. Pass --live (Windows) or --wav-dir DIR to "
    "hear or capture audio; --check runs a diagnostic self-test."
)


class OnboardingCoordinator:
    """Publish a welcome tour once per machine, gated by a sentinel file."""

    SOURCE = "onboarding"

    def __init__(
        self,
        bus: EventBus,
        sentinel_path: Path | str,
        *,
        lines: Optional[Iterable[str]] = None,
        has_live_audio: bool = True,
        hint_stream: Optional[IO[str]] = None,
    ) -> None:
        """Remember the bus, sentinel location, and welcome lines.

        `lines` defaults to `DEFAULT_ONBOARDING_LINES`. Callers can
        override to localise, shorten, or extend the tour without
        touching this module.

        `has_live_audio=False` tells the coordinator to write
        `SILENT_SINK_HINT` to `hint_stream` (default: `sys.stderr`)
        before publishing the tour event. Without that cue, a new user
        on a silent sink (F6 POSIX gap or a plain `python -m asat` with
        no flags) hears nothing and reasonably concludes ASAT is broken.
        """
        self._bus = bus
        self._sentinel_path = Path(sentinel_path)
        self._lines: tuple[str, ...] = (
            tuple(lines) if lines is not None else DEFAULT_ONBOARDING_LINES
        )
        self._has_live_audio = has_live_audio
        self._hint_stream = hint_stream if hint_stream is not None else sys.stderr

    @property
    def sentinel_path(self) -> Path:
        """Return the sentinel path so tests and CLI code can inspect it."""
        return self._sentinel_path

    def is_first_run(self) -> bool:
        """Return True when the sentinel is absent (no prior welcome)."""
        return not self._sentinel_path.exists()

    def run(self, *, force: bool = False) -> bool:
        """Publish the tour and (on a first run) create the sentinel.

        Returns False on a non-first-run, non-forced call so the CLI
        can treat the result as "did I just onboard a newcomer?"
        without re-checking the filesystem itself.

        `force=True` is the `:welcome` replay path (F44): it publishes
        the same `FIRST_RUN_DETECTED` event but does NOT write the
        sentinel, because the sentinel's meaning is "the user has
        seen this once" and a replay must not rewind that fact. The
        silent-sink hint (F41) is also skipped on a forced replay —
        the user chose this; they already know whether they can hear.
        """
        if not force and not self.is_first_run():
            return False
        if not force and not self._has_live_audio:
            print(SILENT_SINK_HINT, file=self._hint_stream)
        publish_event(
            self._bus,
            EventType.FIRST_RUN_DETECTED,
            {
                "lines": list(self._lines),
                "sentinel_path": str(self._sentinel_path),
                "replay": force,
            },
            source=self.SOURCE,
        )
        if force:
            return True
        self._sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        self._sentinel_path.write_text("first-run-done\n", encoding="utf-8")
        return True

    def publish_tour_step(
        self,
        *,
        command: str = FIRST_RUN_TOUR_COMMAND,
        lines: Iterable[str] = FIRST_RUN_TOUR_LINES,
        replay: bool = False,
    ) -> None:
        """Publish F43's `FIRST_RUN_TOUR_STEP` event.

        Application.build calls this exactly once per first run, right
        after pre-populating the notebook's first cell with `command`.
        Kept here (rather than inlined in Application) so the tour's
        command + narration live next to the rest of the onboarding
        vocabulary and can be reused by future tour variants.

        `replay=True` marks the event so subscribers (and tests) can
        distinguish a `:welcome` replay from the genuine first launch.
        """
        publish_event(
            self._bus,
            EventType.FIRST_RUN_TOUR_STEP,
            {"command": command, "lines": list(lines), "replay": replay},
            source=self.SOURCE,
        )

    def publish_event_log_preview_beat(
        self,
        *,
        lines: Iterable[str] = FIRST_RUN_EVENT_LOG_LINES,
        replay: bool = False,
    ) -> None:
        """Third tour beat: introduce the event log viewer (F39).

        Narrates the Ctrl+E keystroke and the Up/Down/`t` affordances
        without actually pulsing the viewer — opening it under the user
        mid-tour would thrash focus and leave them in EVENT_LOG mode
        waiting for input. A narration-only beat teaches the keystroke
        without hijacking the notebook.
        """
        publish_event(
            self._bus,
            EventType.FIRST_RUN_TOUR_EVENT_LOG_PREVIEW,
            {"lines": list(lines), "replay": replay},
            source=self.SOURCE,
        )

    def publish_log_path_beat(
        self,
        path: Optional[str] = None,
        *,
        lines: Iterable[str] = FIRST_RUN_LOG_PATH_LINES,
        replay: bool = False,
    ) -> None:
        """Fourth tour beat: tell the user where the grouped log file lives.

        ``path=None`` (or empty string) means no file logger is
        attached — happens when ASAT was launched without a workspace
        and without an explicit log directory. The event still fires
        (so subscribers can notice the beat) but with an empty path
        string so the default narration can skip the announcement.
        """
        publish_event(
            self._bus,
            EventType.FIRST_RUN_TOUR_LOG_PATH,
            {
                "path": path or "",
                "lines": list(lines),
                "replay": replay,
            },
            source=self.SOURCE,
        )

    def publish_tour_completed_beat(
        self,
        *,
        lines: Iterable[str] = FIRST_RUN_COMPLETED_LINES,
        replay: bool = False,
    ) -> None:
        """Final tour beat: tour complete, return the user to the prompt.

        Fires exactly once per run. Tests assert on the terminator so
        fixtures can wait for the tour to finish without sleeping.
        """
        publish_event(
            self._bus,
            EventType.FIRST_RUN_TOUR_COMPLETED,
            {"lines": list(lines), "replay": replay},
            source=self.SOURCE,
        )

    def reset(self) -> None:
        """Delete the sentinel so the next `.run()` re-fires the tour."""
        if self._sentinel_path.exists():
            self._sentinel_path.unlink()
