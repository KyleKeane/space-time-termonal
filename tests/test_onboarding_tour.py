"""Unit tests for the PR 4 scripted first-run tour."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asat.app import Application
from asat.cell import CellKind
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.onboarding import (
    FIRST_RUN_COMPLETED_LINES,
    FIRST_RUN_EVENT_LOG_LINES,
    FIRST_RUN_LOG_PATH_LINES,
    FIRST_RUN_OUTLINE_HEADINGS,
    FIRST_RUN_TOUR_COMMAND,
    OnboardingCoordinator,
)


SCRIPTED_TOUR_EVENTS = (
    EventType.FIRST_RUN_TOUR_STEP,
    EventType.FIRST_RUN_TOUR_EVENT_LOG_PREVIEW,
    EventType.FIRST_RUN_TOUR_LOG_PATH,
    EventType.FIRST_RUN_TOUR_COMPLETED,
)


class _Recorder:
    """Collect every event so tests can filter by type."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]

    def types_in_order(self) -> list[EventType]:
        return [e.event_type for e in self.events]


class OnboardingBeatPublisherTests(unittest.TestCase):
    """The new beat-publishing methods on OnboardingCoordinator."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.sentinel = Path(self._tempdir.name) / "first-run-done"

    def test_event_log_preview_beat_publishes_default_lines(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_event_log_preview_beat()

        events = recorder.of(EventType.FIRST_RUN_TOUR_EVENT_LOG_PREVIEW)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["lines"], list(FIRST_RUN_EVENT_LOG_LINES))
        self.assertFalse(payload["replay"])

    def test_log_path_beat_carries_path_when_provided(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_log_path_beat("/tmp/events.log")

        events = recorder.of(EventType.FIRST_RUN_TOUR_LOG_PATH)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["path"], "/tmp/events.log")
        self.assertEqual(payload["lines"], list(FIRST_RUN_LOG_PATH_LINES))

    def test_log_path_beat_normalises_none_to_empty_string(self) -> None:
        """The predicate on the default binding gates on `path != ''`; a
        `None` from Application.build (no file logger) must round-trip
        as `""` so the binding drops the beat rather than crashing."""
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_log_path_beat(None)

        payload = recorder.of(EventType.FIRST_RUN_TOUR_LOG_PATH)[0].payload
        self.assertEqual(payload["path"], "")

    def test_tour_completed_beat_carries_replay_marker(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_tour_completed_beat(replay=True)

        events = recorder.of(EventType.FIRST_RUN_TOUR_COMPLETED)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["lines"], list(FIRST_RUN_COMPLETED_LINES))
        self.assertTrue(payload["replay"])

    def test_publish_tour_step_accepts_replay_kwarg(self) -> None:
        """The F43 tour step gains a `replay` marker so the `:welcome`
        replay can be distinguished from the genuine first-run fire."""
        bus = EventBus()
        recorder = _Recorder(bus)
        coordinator = OnboardingCoordinator(bus, self.sentinel)

        coordinator.publish_tour_step(replay=True)

        payload = recorder.of(EventType.FIRST_RUN_TOUR_STEP)[0].payload
        self.assertTrue(payload["replay"])


class ApplicationFirstRunTourTests(unittest.TestCase):
    """Application.build drives the full four-beat tour on first run."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.sentinel = Path(self._tempdir.name) / "first-run-done"
        self.log_dir = Path(self._tempdir.name) / "log"

    def _factory(self, seen: list[Event]):
        def _build(bus: EventBus) -> OnboardingCoordinator:
            bus.subscribe("*", seen.append)
            return OnboardingCoordinator(bus, self.sentinel)
        return _build

    def test_first_run_seeds_outline_and_command_cell(self) -> None:
        seen: list[Event] = []
        app = Application.build(onboarding_factory=self._factory(seen))
        self.addCleanup(app.close)

        cells = app.session.cells
        self.assertEqual(len(cells), len(FIRST_RUN_OUTLINE_HEADINGS) + 1)
        for cell, (level, title) in zip(cells, FIRST_RUN_OUTLINE_HEADINGS):
            self.assertEqual(cell.kind, CellKind.HEADING)
            self.assertEqual(cell.heading_level, level)
            self.assertEqual(cell.heading_title, title)
        self.assertEqual(cells[-1].kind, CellKind.COMMAND)
        self.assertEqual(cells[-1].command, FIRST_RUN_TOUR_COMMAND)

    def test_first_run_fires_all_four_scripted_beats_in_order(self) -> None:
        seen: list[Event] = []
        app = Application.build(onboarding_factory=self._factory(seen))
        self.addCleanup(app.close)

        types = [e.event_type for e in seen]
        welcome_idx = types.index(EventType.FIRST_RUN_DETECTED)
        positions = [types.index(t) for t in SCRIPTED_TOUR_EVENTS]
        self.assertLess(welcome_idx, positions[0])
        self.assertEqual(sorted(positions), positions)

    def test_first_run_scripted_beats_are_not_marked_replay(self) -> None:
        seen: list[Event] = []
        app = Application.build(onboarding_factory=self._factory(seen))
        self.addCleanup(app.close)

        for event_type in SCRIPTED_TOUR_EVENTS:
            events = [e for e in seen if e.event_type == event_type]
            self.assertEqual(len(events), 1)
            self.assertFalse(events[0].payload["replay"])

    def test_first_run_log_path_beat_carries_file_path_when_configured(self) -> None:
        """When an event-log directory is wired, the log-path beat
        carries the concrete path so the narrator can announce it."""
        seen: list[Event] = []
        app = Application.build(
            onboarding_factory=self._factory(seen),
            event_log_dir=self.log_dir,
        )
        self.addCleanup(app.close)

        event = [
            e for e in seen if e.event_type == EventType.FIRST_RUN_TOUR_LOG_PATH
        ][0]
        self.assertTrue(event.payload["path"].endswith(".log"))
        self.assertIn(str(self.log_dir), event.payload["path"])

    def test_first_run_log_path_beat_is_empty_without_file_logger(self) -> None:
        seen: list[Event] = []
        app = Application.build(onboarding_factory=self._factory(seen))
        self.addCleanup(app.close)

        event = [
            e for e in seen if e.event_type == EventType.FIRST_RUN_TOUR_LOG_PATH
        ][0]
        self.assertEqual(event.payload["path"], "")

    def test_returning_user_does_not_fire_scripted_beats(self) -> None:
        """Sentinel-exists path must skip every post-welcome beat so a
        returning user does not hear the tour on every launch."""
        self.sentinel.write_text("done\n", encoding="utf-8")
        seen: list[Event] = []
        app = Application.build(onboarding_factory=self._factory(seen))
        self.addCleanup(app.close)

        types = [e.event_type for e in seen]
        self.assertNotIn(EventType.FIRST_RUN_TOUR_STEP, types)
        for beat in SCRIPTED_TOUR_EVENTS:
            self.assertNotIn(beat, types)

    def test_welcome_meta_command_replays_every_scripted_beat(self) -> None:
        """`:welcome` must re-publish every tour event with replay=True
        and must not re-seed cells (that would stomp the user's work)."""
        self.sentinel.write_text("done\n", encoding="utf-8")

        def _factory(bus: EventBus) -> OnboardingCoordinator:
            return OnboardingCoordinator(bus, self.sentinel)

        app = Application.build(onboarding_factory=_factory)
        self.addCleanup(app.close)
        cell_count_before = len(app.session.cells)

        replays: list[Event] = []
        app.bus.subscribe("*", replays.append)
        from asat.keys import Key
        for ch in ":welcome":
            app.handle_key(Key.printable(ch))
        app.handle_key(Key.special("enter"))

        # FIRST_RUN_DETECTED + all four scripted beats all fire with replay=True.
        expected = (EventType.FIRST_RUN_DETECTED,) + SCRIPTED_TOUR_EVENTS
        for event_type in expected:
            events = [e for e in replays if e.event_type == event_type]
            self.assertEqual(len(events), 1, f"missing replay of {event_type}")
            self.assertTrue(events[0].payload["replay"])

        # No extra cells got seeded.
        self.assertEqual(len(app.session.cells), cell_count_before)

    def test_welcome_without_onboarding_does_not_crash(self) -> None:
        """--quiet / --check paths leave `onboarding=None`; `:welcome`
        there must stay a harmless no-op."""
        app = Application.build()  # no onboarding_factory
        self.addCleanup(app.close)

        from asat.keys import Key
        for ch in ":welcome":
            app.handle_key(Key.printable(ch))
        app.handle_key(Key.special("enter"))

        self.assertTrue(app.running)


if __name__ == "__main__":
    unittest.main()
