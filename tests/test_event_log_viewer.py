"""Unit tests for the F39 EventLogViewer."""

from __future__ import annotations

import unittest

from asat.audio_sink import MemorySink
from asat.default_bank import default_sound_bank
from asat.event_bus import EventBus, publish_event
from asat.event_log import (
    DEFAULT_MAX_ENTRIES,
    QUICK_EDIT_FIELDS,
    EventLogError,
    EventLogViewer,
)
from asat.events import EventType
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice
from asat.sound_engine import SoundEngine


def _bank_with_spoken_binding() -> SoundBank:
    """A bank with exactly one binding that speaks COMMAND_COMPLETED."""
    return SoundBank(
        voices=(Voice(id="narrator", rate=1.0),),
        sounds=(SoundRecipe(id="tick", kind="tone", params={"frequency": 440.0}),),
        bindings=(
            EventBinding(
                id="command_completed",
                event_type=EventType.COMMAND_COMPLETED.value,
                voice_id="narrator",
                sound_id="tick",
                say_template="done exit {exit_code}",
                priority=100,
            ),
        ),
    )


def _engine(bus: EventBus, bank: SoundBank | None = None) -> SoundEngine:
    return SoundEngine(bus, bank or _bank_with_spoken_binding(), MemorySink())


class RingBufferTests(unittest.TestCase):

    def test_captures_events_as_entries(self) -> None:
        bus = EventBus()
        engine = _engine(bus)
        viewer = EventLogViewer(bus, engine)
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="test",
        )
        entries = viewer.entries
        # The viewer records COMMAND_COMPLETED and also the AUDIO_SPOKEN
        # that the sound engine emits in response to it.
        event_types = [e.event_type for e in entries]
        self.assertIn(EventType.COMMAND_COMPLETED.value, event_types)
        self.assertIn(EventType.AUDIO_SPOKEN.value, event_types)

    def test_binding_id_set_for_audio_spoken(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="test",
        )
        spoken = [e for e in viewer.entries if e.event_type == "audio.spoken"]
        self.assertTrue(spoken)
        self.assertEqual(spoken[-1].binding_id, "command_completed")

    def test_ring_buffer_respects_max_entries(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus), max_entries=3)
        for i in range(5):
            publish_event(bus, EventType.HELP_REQUESTED, {"n": i}, source="t")
        self.assertLessEqual(len(viewer.entries), 3)
        # Newest retained, oldest evicted.
        self.assertEqual(viewer.entries[-1].payload["n"], 4)

    def test_self_source_events_filtered(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        viewer.open()  # Publishes EVENT_LOG_OPENED from viewer.SOURCE.
        viewer.focus_latest()  # Publishes EVENT_LOG_FOCUSED.
        # Neither of those should land in the ring buffer — they'd drown
        # out the tail every time Ctrl+E fired.
        types = {e.event_type for e in viewer.entries}
        self.assertNotIn(EventType.EVENT_LOG_OPENED.value, types)
        self.assertNotIn(EventType.EVENT_LOG_FOCUSED.value, types)

    def test_default_max_entries_is_reasonable(self) -> None:
        self.assertEqual(DEFAULT_MAX_ENTRIES, 200)

    def test_zero_max_entries_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EventLogViewer(EventBus(), _engine(EventBus()), max_entries=0)


class NavigationTests(unittest.TestCase):

    def test_open_focuses_latest_and_publishes_opened(self) -> None:
        bus = EventBus()
        opened: list[object] = []
        bus.subscribe(EventType.EVENT_LOG_OPENED, opened.append)
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 1}, source="t")
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 2}, source="t")
        viewer.open()
        self.assertTrue(viewer.is_open)
        self.assertEqual(viewer.focus_index, len(viewer.entries) - 1)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].payload["count"], len(viewer.entries))

    def test_focus_previous_walks_back_and_clamps(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        for i in range(3):
            publish_event(bus, EventType.HELP_REQUESTED, {"n": i}, source="t")
        viewer.open()
        start = viewer.focus_index
        viewer.focus_previous()
        self.assertEqual(viewer.focus_index, start - 1)
        viewer.focus_previous()
        viewer.focus_previous()
        viewer.focus_previous()  # Clamps at 0.
        self.assertEqual(viewer.focus_index, 0)

    def test_focus_next_clamps_at_tail(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 0}, source="t")
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 1}, source="t")
        viewer.open()
        tail = viewer.focus_index
        viewer.focus_next()  # Already at tail.
        self.assertEqual(viewer.focus_index, tail)

    def test_empty_focus_latest_noops(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        viewer.focus_latest()
        self.assertIsNone(viewer.focus_index)

    def test_focus_stays_at_tail_when_focus_followed_tail(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 0}, source="t")
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 1}, source="t")
        # Focus tracks the tail implicitly — appending a new event
        # should move it along.
        publish_event(bus, EventType.HELP_REQUESTED, {"n": 2}, source="t")
        self.assertEqual(viewer.focus_index, len(viewer.entries) - 1)


class QuickEditTests(unittest.TestCase):

    def test_begin_quick_edit_returns_none_when_no_binding(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(
            bus, EventType.HELP_REQUESTED, {"lines": ["x"]}, source="t"
        )
        viewer.open()
        self.assertIsNone(viewer.begin_quick_edit())

    def test_begin_quick_edit_rotates_fields(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="t",
        )
        viewer.open()
        # Focus the audio.spoken entry (it has binding_id).
        while viewer.selected_entry() is None or viewer.selected_entry().binding_id is None:
            viewer.focus_previous()
            if viewer.focus_index == 0:
                break
        first = viewer.begin_quick_edit()
        self.assertEqual(first, QUICK_EDIT_FIELDS[0])
        second = viewer.begin_quick_edit()
        self.assertEqual(second, QUICK_EDIT_FIELDS[1])

    def test_commit_quick_edit_updates_bank(self) -> None:
        bus = EventBus()
        engine = _engine(bus)
        viewer = EventLogViewer(bus, engine)
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="t",
        )
        viewer.open()
        # Move to the audio.spoken entry.
        for entry in reversed(viewer.entries):
            if entry.binding_id is not None:
                break
        else:
            self.fail("no binding entry")
        while viewer.selected_entry().binding_id is None:
            viewer.focus_previous()
        viewer.begin_quick_edit()  # say_template by default
        viewer._quick_edit_buffer = ""
        for ch in "ran it":
            viewer.extend_quick_edit(ch)
        committed: list[object] = []
        bus.subscribe(
            EventType.EVENT_LOG_QUICK_EDIT_COMMITTED, committed.append
        )
        updated = viewer.commit_quick_edit()
        self.assertIsNotNone(updated)
        self.assertEqual(updated.say_template, "ran it")
        updated_in_bank = next(
            b for b in engine.bank.bindings if b.id == "command_completed"
        )
        self.assertEqual(updated_in_bank.say_template, "ran it")
        self.assertEqual(len(committed), 1)

    def test_commit_quick_edit_invalid_value_raises(self) -> None:
        bus = EventBus()
        engine = _engine(bus)
        viewer = EventLogViewer(bus, engine)
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="t",
        )
        viewer.open()
        while viewer.selected_entry().binding_id is None:
            viewer.focus_previous()
        # Rotate to `enabled`, then type a bad value.
        viewer.begin_quick_edit()  # say_template
        viewer.begin_quick_edit()  # voice_id
        viewer.begin_quick_edit()  # enabled
        self.assertEqual(viewer.quick_edit_field, "enabled")
        viewer._quick_edit_buffer = "gibberish"
        with self.assertRaises(EventLogError):
            viewer.commit_quick_edit()

    def test_cancel_quick_edit_clears_state(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="t",
        )
        viewer.open()
        while viewer.selected_entry().binding_id is None:
            viewer.focus_previous()
        viewer.begin_quick_edit()
        viewer.extend_quick_edit("x")
        viewer.cancel_quick_edit()
        self.assertIsNone(viewer.quick_edit_field)
        self.assertEqual(viewer.quick_edit_buffer, "")


class ReplayTests(unittest.TestCase):

    def test_replay_republishes_event_with_replay_marker(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        publish_event(
            bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c1", "exit_code": 0},
            source="t",
        )
        viewer.open()
        # Focus on the COMMAND_COMPLETED entry.
        for i, entry in enumerate(viewer.entries):
            if entry.event_type == EventType.COMMAND_COMPLETED.value:
                viewer._focus_index = i
                break
        captured: list[object] = []
        bus.subscribe(EventType.COMMAND_COMPLETED, captured.append)
        replayed_marker: list[object] = []
        bus.subscribe(EventType.EVENT_LOG_REPLAYED, replayed_marker.append)
        viewer.replay_selected()
        self.assertTrue(captured)
        self.assertTrue(captured[-1].payload.get("replay") is True)
        self.assertEqual(len(replayed_marker), 1)

    def test_replay_with_no_selection_returns_none(self) -> None:
        bus = EventBus()
        viewer = EventLogViewer(bus, _engine(bus))
        self.assertIsNone(viewer.replay_selected())


class CloseTests(unittest.TestCase):

    def test_close_publishes_closed(self) -> None:
        bus = EventBus()
        closed: list[object] = []
        bus.subscribe(EventType.EVENT_LOG_CLOSED, closed.append)
        viewer = EventLogViewer(bus, _engine(bus))
        viewer.open()
        viewer.close()
        self.assertFalse(viewer.is_open)
        self.assertEqual(len(closed), 1)

    def test_close_is_idempotent(self) -> None:
        bus = EventBus()
        closed: list[object] = []
        bus.subscribe(EventType.EVENT_LOG_CLOSED, closed.append)
        viewer = EventLogViewer(bus, _engine(bus))
        viewer.close()  # Never opened.
        viewer.open()
        viewer.close()
        viewer.close()  # Already closed.
        self.assertEqual(len(closed), 1)


if __name__ == "__main__":
    unittest.main()
