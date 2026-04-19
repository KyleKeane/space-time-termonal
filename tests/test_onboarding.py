"""Unit tests for OnboardingCoordinator (F20)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.onboarding import DEFAULT_ONBOARDING_LINES, OnboardingCoordinator


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
