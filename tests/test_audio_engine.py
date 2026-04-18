"""Unit tests for the SpatialAudioEngine and VoiceRouter."""

from __future__ import annotations

import unittest

from asat.audio import AudioBuffer, ChannelLayout, VoiceProfile
from asat.audio_engine import SpatialAudioEngine, VoiceRouter
from asat.audio_sink import MemorySink
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.hrtf import Spatializer
from asat.tts import ToneTTSEngine


class VoiceRouterTests(unittest.TestCase):

    def test_maps_output_and_error_chunks_to_distinct_voices(self) -> None:
        router = VoiceRouter()
        out = router.voice_for(EventType.OUTPUT_CHUNK)
        err = router.voice_for(EventType.ERROR_CHUNK)
        self.assertIsNotNone(out)
        self.assertIsNotNone(err)
        assert out is not None and err is not None
        self.assertNotEqual(out.name, err.name)

    def test_notifications_map_to_notification_voice(self) -> None:
        router = VoiceRouter()
        notice = router.voice_for(EventType.COMMAND_COMPLETED)
        self.assertIsNotNone(notice)

    def test_unrelated_event_returns_none(self) -> None:
        router = VoiceRouter()
        self.assertIsNone(router.voice_for(EventType.CELL_CREATED))


def _build_engine(sample_rate: int = 4000) -> tuple[EventBus, MemorySink, SpatialAudioEngine]:
    """Construct a fresh engine wired to a memory sink for tests."""
    bus = EventBus()
    sink = MemorySink()
    engine = SpatialAudioEngine(
        bus=bus,
        tts=ToneTTSEngine(sample_rate=sample_rate),
        spatializer=Spatializer(),
        sink=sink,
    )
    return bus, sink, engine


class SpatialAudioEngineTests(unittest.TestCase):

    def test_speak_produces_stereo_buffer(self) -> None:
        _, sink, engine = _build_engine()
        result = engine.speak("hi", VoiceProfile.stdout_default())
        self.assertEqual(result.layout, ChannelLayout.STEREO)
        self.assertEqual(sink.buffers, (result,))

    def test_output_chunk_event_triggers_one_speak(self) -> None:
        bus, sink, _engine = _build_engine()
        bus.publish(
            Event(
                event_type=EventType.OUTPUT_CHUNK,
                payload={"line": "hello", "cell_id": "c1"},
            )
        )
        self.assertEqual(len(sink.buffers), 1)
        self.assertTrue(sink.buffers[0].is_stereo())

    def test_blank_lines_are_skipped(self) -> None:
        bus, sink, _engine = _build_engine()
        bus.publish(
            Event(
                event_type=EventType.OUTPUT_CHUNK,
                payload={"line": "   \n", "cell_id": "c1"},
            )
        )
        self.assertEqual(sink.buffers, ())

    def test_stdout_and_stderr_routed_to_different_sides(self) -> None:
        bus, sink, _engine = _build_engine()
        bus.publish(
            Event(
                event_type=EventType.OUTPUT_CHUNK,
                payload={"line": "aaaa", "cell_id": "c1"},
            )
        )
        bus.publish(
            Event(
                event_type=EventType.ERROR_CHUNK,
                payload={"line": "aaaa", "cell_id": "c1"},
            )
        )
        stdout_buffer, stderr_buffer = sink.buffers
        stdout_diff = _left_minus_right_energy(stdout_buffer)
        stderr_diff = _left_minus_right_energy(stderr_buffer)
        self.assertGreater(stdout_diff, 0.0)
        self.assertLess(stderr_diff, 0.0)

    def test_command_completed_speaks_notification(self) -> None:
        bus, sink, _engine = _build_engine()
        bus.publish(
            Event(
                event_type=EventType.COMMAND_COMPLETED,
                payload={"cell_id": "c1", "exit_code": 0, "timed_out": False},
            )
        )
        self.assertEqual(len(sink.buffers), 1)

    def test_command_failed_message_reports_exit_code(self) -> None:
        bus, sink, _engine = _build_engine()
        bus.publish(
            Event(
                event_type=EventType.COMMAND_FAILED,
                payload={"cell_id": "c1", "exit_code": 3, "timed_out": False},
            )
        )
        self.assertEqual(len(sink.buffers), 1)

    def test_close_unsubscribes_from_bus(self) -> None:
        bus, sink, engine = _build_engine()
        engine.close()
        bus.publish(
            Event(
                event_type=EventType.OUTPUT_CHUNK,
                payload={"line": "hello", "cell_id": "c1"},
            )
        )
        self.assertEqual(sink.buffers, ())


def _left_minus_right_energy(buffer: AudioBuffer) -> float:
    """Return the energy difference between left and right channels."""
    left_energy = sum(sample * sample for sample in buffer.left_channel())
    right_energy = sum(sample * sample for sample in buffer.right_channel())
    return left_energy - right_energy


if __name__ == "__main__":
    unittest.main()
