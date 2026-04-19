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

from pathlib import Path
from typing import Iterable, Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType


DEFAULT_ONBOARDING_LINES: tuple[str, ...] = (
    "Welcome to the Accessible Spatial Audio Terminal.",
    "Type colon, h, e, l, p, Enter for the keystroke cheat sheet.",
    "Type colon, c, o, m, m, a, n, d, s, Enter to list every meta-command.",
    "Press Escape any time to return to notebook mode.",
    "Type colon, q, u, i, t, Enter to exit when you are done.",
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
    ) -> None:
        """Remember the bus, sentinel location, and welcome lines.

        `lines` defaults to `DEFAULT_ONBOARDING_LINES`. Callers can
        override to localise, shorten, or extend the tour without
        touching this module.
        """
        self._bus = bus
        self._sentinel_path = Path(sentinel_path)
        self._lines: tuple[str, ...] = (
            tuple(lines) if lines is not None else DEFAULT_ONBOARDING_LINES
        )

    @property
    def sentinel_path(self) -> Path:
        """Return the sentinel path so tests and CLI code can inspect it."""
        return self._sentinel_path

    def is_first_run(self) -> bool:
        """Return True when the sentinel is absent (no prior welcome)."""
        return not self._sentinel_path.exists()

    def run(self) -> bool:
        """Publish the tour once and create the sentinel; return True if fired.

        Returns False on subsequent calls so the CLI can treat the
        result as "did I just onboard a newcomer?" without re-checking
        the filesystem itself.
        """
        if not self.is_first_run():
            return False
        publish_event(
            self._bus,
            EventType.FIRST_RUN_DETECTED,
            {
                "lines": list(self._lines),
                "sentinel_path": str(self._sentinel_path),
            },
            source=self.SOURCE,
        )
        self._sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        self._sentinel_path.write_text("first-run-done\n", encoding="utf-8")
        return True

    def reset(self) -> None:
        """Delete the sentinel so the next `.run()` re-fires the tour."""
        if self._sentinel_path.exists():
            self._sentinel_path.unlink()
