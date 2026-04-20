"""Unit tests for OnboardingCoordinator (F20, F41)."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.onboarding import (
    DEFAULT_ONBOARDING_LINES,
    FIRST_RUN_TOUR_COMMAND,
    FIRST_RUN_TOUR_LINES,
    SILENT_SINK_HINT,
    OnboardingCoordinator,
)


class _Recorder:
    """Collect every event so tests can filter by type."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


class OnboardingCoordinatorTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.sentinel = Path(self._tempdir.name) / "asat" / "first-run-done"

    def test_first_run_publishes_event_and_creates_sentinel(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        self.assertTrue(coordinator.is_first_run())
        fired = coordinator.run()

        self.assertTrue(fired)
        events = recorder.of(EventType.FIRST_RUN_DETECTED)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["lines"], list(DEFAULT_ONBOARDING_LINES))
        self.assertEqual(payload["sentinel_path"], str(self.sentinel))
        self.assertTrue(self.sentinel.exists())
        self.assertFalse(coordinator.is_first_run())

    def test_second_run_is_silent_noop(self) -> None:
        bus = EventBus()
        coordinator = OnboardingCoordinator(bus, self.sentinel)
        coordinator.run()

        recorder = _Recorder(bus)  # Recorder after first run to catch round-2.
        fired = coordinator.run()

        self.assertFalse(fired)
        self.assertEqual(recorder.of(EventType.FIRST_RUN_DETECTED), [])

    def test_run_creates_missing_parent_directories(self) -> None:
        bus = EventBus()
        nested = Path(self._tempdir.name) / "deeply" / "nested" / "done"
        coordinator = OnboardingCoordinator(bus, nested)

        coordinator.run()

        self.assertTrue(nested.exists())
        self.assertTrue(nested.parent.is_dir())

    def test_custom_lines_are_forwarded(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        custom = ("one", "two", "three")
        coordinator = OnboardingCoordinator(bus, self.sentinel, lines=custom)

        coordinator.run()

        payload = recorder.of(EventType.FIRST_RUN_DETECTED)[0].payload
        self.assertEqual(payload["lines"], list(custom))

    def test_reset_clears_sentinel(self) -> None:
        bus = EventBus()
        coordinator = OnboardingCoordinator(bus, self.sentinel)
        coordinator.run()
        self.assertFalse(coordinator.is_first_run())

        coordinator.reset()

        self.assertTrue(coordinator.is_first_run())
        self.assertFalse(self.sentinel.exists())

    def test_reset_is_safe_when_sentinel_is_missing(self) -> None:
        bus = EventBus()
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.reset()  # Must not raise.

        self.assertTrue(coordinator.is_first_run())

    def test_sentinel_path_property_returns_path_object(self) -> None:
        bus = EventBus()
        coordinator = OnboardingCoordinator(bus, str(self.sentinel))
        self.assertEqual(coordinator.sentinel_path, self.sentinel)

    def test_silent_sink_writes_hint_before_publishing(self) -> None:
        # F41: a first-time user on a silent sink must be told in
        # plain text that the welcome tour is about to disappear into
        # a buffer they will never hear, so they do not conclude ASAT
        # is broken. The hint lands on the supplied stream before the
        # event fires so screen readers speaking stderr pick it up in
        # the same moment as the audio would have played.
        bus = EventBus()
        recorder = _Recorder(bus)
        stream = io.StringIO()
        coordinator = OnboardingCoordinator(
            bus,
            self.sentinel,
            has_live_audio=False,
            hint_stream=stream,
        )

        fired = coordinator.run()

        self.assertTrue(fired)
        self.assertIn(SILENT_SINK_HINT, stream.getvalue())
        # And the tour event still fires so the existing narration
        # path is unchanged when a sink is later attached.
        self.assertEqual(len(recorder.of(EventType.FIRST_RUN_DETECTED)), 1)

    def test_live_audio_suppresses_silent_sink_hint(self) -> None:
        # When the user has told us where audio goes (via --live or
        # --wav-dir), the coordinator must stay silent on stderr or
        # the launch banner will be cluttered with a warning that
        # does not apply.
        bus = EventBus()
        stream = io.StringIO()
        coordinator = OnboardingCoordinator(
            bus,
            self.sentinel,
            has_live_audio=True,
            hint_stream=stream,
        )

        coordinator.run()

        self.assertEqual(stream.getvalue(), "")

    def test_silent_sink_hint_is_first_run_only(self) -> None:
        # Second launch on a silent sink must not repeat the hint.
        # Regressing this would spam stderr every time ASAT starts
        # without --live on POSIX, which is the daily path until F6.
        bus = EventBus()
        stream = io.StringIO()
        coordinator = OnboardingCoordinator(
            bus,
            self.sentinel,
            has_live_audio=False,
            hint_stream=stream,
        )
        coordinator.run()
        stream.truncate(0)
        stream.seek(0)

        fired_again = coordinator.run()

        self.assertFalse(fired_again)
        self.assertEqual(stream.getvalue(), "")

    def test_force_run_fires_even_when_sentinel_exists(self) -> None:
        """F44: `.run(force=True)` replays the tour after the first
        run. A user invoking `:welcome` must hear the same lines the
        sentinel saved them from hearing on every launch."""
        bus = EventBus()
        coordinator = OnboardingCoordinator(bus, self.sentinel)
        coordinator.run()
        self.assertFalse(coordinator.is_first_run())

        recorder = _Recorder(bus)
        fired = coordinator.run(force=True)

        self.assertTrue(fired)
        events = recorder.of(EventType.FIRST_RUN_DETECTED)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].payload["replay"])
        self.assertEqual(
            events[0].payload["lines"], list(DEFAULT_ONBOARDING_LINES)
        )

    def test_force_run_does_not_rewrite_sentinel(self) -> None:
        """F44: a replay must not touch the sentinel. Its meaning
        stays `the user has seen the tour once` — rewinding that would
        break F20's once-per-machine contract."""
        bus = EventBus()
        coordinator = OnboardingCoordinator(bus, self.sentinel)
        coordinator.run()
        first_mtime = self.sentinel.stat().st_mtime_ns

        coordinator.run(force=True)
        coordinator.run(force=True)

        self.assertEqual(self.sentinel.stat().st_mtime_ns, first_mtime)

    def test_force_run_skips_silent_sink_hint(self) -> None:
        """F44 + F41: the replay must not print the silent-sink hint.
        The user chose to replay; they already know whether they
        can hear. Spamming stderr on every `:welcome` would be noise."""
        bus = EventBus()
        stream = io.StringIO()
        coordinator = OnboardingCoordinator(
            bus,
            self.sentinel,
            has_live_audio=False,
            hint_stream=stream,
        )

        coordinator.run(force=True)

        self.assertEqual(stream.getvalue(), "")

    def test_publish_tour_step_fires_first_run_tour_step_event(self) -> None:
        """F43: the coordinator publishes FIRST_RUN_TOUR_STEP with the
        pre-filled command and the short prompt lines so the default
        bank can narrate them."""
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_tour_step()

        events = recorder.of(EventType.FIRST_RUN_TOUR_STEP)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["command"], FIRST_RUN_TOUR_COMMAND)
        self.assertEqual(payload["lines"], list(FIRST_RUN_TOUR_LINES))

    def test_publish_tour_step_accepts_custom_command(self) -> None:
        """Callers can override the tour command for localisation or
        environment-specific seeds (e.g. `Get-Date` on PowerShell)."""
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_tour_step(command="Get-Date", lines=("Press Enter.",))

        payload = recorder.of(EventType.FIRST_RUN_TOUR_STEP)[0].payload
        self.assertEqual(payload["command"], "Get-Date")
        self.assertEqual(payload["lines"], ["Press Enter."])

    def test_default_lines_mention_help_and_quit(self) -> None:
        # Sanity check the welcome text a newcomer will actually hear
        # so a careless edit doesn't ship a tour without the two
        # instructions that unstick every stuck user. The tour spells
        # out meta-commands letter-by-letter so TTS pronounces each
        # character; we check for the spelled form ("h, e, l, p") and
        # the "cheat sheet" / "exit" anchors it names.
        joined = " ".join(DEFAULT_ONBOARDING_LINES).lower()
        self.assertIn("h, e, l, p", joined)
        self.assertIn("q, u, i, t", joined)
        self.assertIn("cheat sheet", joined)
        self.assertIn("escape", joined)


if __name__ == "__main__":
    unittest.main()
