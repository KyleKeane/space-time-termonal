"""Unit tests for the data-driven SoundEngine."""

from __future__ import annotations

import unittest

from asat.audio import AudioBuffer, ChannelLayout, VoiceProfile
from asat.audio_sink import MemorySink
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice
from asat.sound_engine import (
    DefaultPredicateEvaluator,
    SoundEngine,
    SoundEngineError,
)
from asat.tts import ToneTTSEngine


class _RecordingTTS:
    """TTS wrapper that records every VoiceProfile it renders with."""

    def __init__(self, sample_rate: int = 8000) -> None:
        self._inner = ToneTTSEngine(sample_rate=sample_rate)
        self.calls: list[VoiceProfile] = []

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        self.calls.append(voice)
        return self._inner.synthesize(text, voice)


def _voice(**overrides) -> Voice:
    """Build a test voice with deterministic defaults."""
    params = dict(id="narrator", rate=1.0, pitch=1.0, volume=1.0, azimuth=0.0, elevation=0.0)
    params.update(overrides)
    return Voice(**params)


def _tone(**overrides) -> SoundRecipe:
    """Build a short tone recipe with deterministic defaults."""
    params = {"frequency": 440.0, "duration": 0.01, "attack": 0.0, "release": 0.0}
    kwargs = dict(id="ding", kind="tone", params=params, volume=1.0)
    kwargs.update(overrides)
    return SoundRecipe(**kwargs)


class PredicateTests(unittest.TestCase):

    def setUp(self) -> None:
        self.evaluator = DefaultPredicateEvaluator()

    def test_empty_predicate_always_matches(self) -> None:
        self.assertTrue(self.evaluator.matches("", {}))
        self.assertTrue(self.evaluator.matches("   ", {"anything": 1}))

    def test_equality_on_strings(self) -> None:
        self.assertTrue(self.evaluator.matches("stream == 'stderr'", {"stream": "stderr"}))
        self.assertFalse(self.evaluator.matches("stream == 'stderr'", {"stream": "stdout"}))

    def test_equality_on_booleans(self) -> None:
        self.assertTrue(self.evaluator.matches("timed_out == True", {"timed_out": True}))
        self.assertFalse(self.evaluator.matches("timed_out == True", {"timed_out": False}))

    def test_inequality(self) -> None:
        self.assertTrue(self.evaluator.matches("exit_code != 0", {"exit_code": 2}))
        self.assertFalse(self.evaluator.matches("exit_code != 0", {"exit_code": 0}))

    def test_in_list(self) -> None:
        self.assertTrue(
            self.evaluator.matches("stream in ['stdout', 'stderr']", {"stream": "stderr"})
        )
        self.assertFalse(
            self.evaluator.matches("stream in ['stdout', 'stderr']", {"stream": "other"})
        )

    def test_missing_key_in_payload_fails_equality(self) -> None:
        self.assertFalse(self.evaluator.matches("stream == 'stderr'", {}))

    def test_unknown_operator_raises(self) -> None:
        with self.assertRaises(SoundEngineError):
            self.evaluator.matches("stream ~= 'weird'", {"stream": "x"})


class SoundEngineDispatchTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.sink = MemorySink()

    def test_speaking_binding_produces_one_buffer(self) -> None:
        voice = _voice()
        binding = EventBinding(
            id="b1",
            event_type=EventType.CELL_CREATED.value,
            voice_id="narrator",
            say_template="new cell",
        )
        bank = SoundBank(voices=(voice,), bindings=(binding,))
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {"cell_id": "c1"}, source="notebook")
        self.assertEqual(len(self.sink.buffers), 1)
        self.assertEqual(self.sink.buffers[0].layout, ChannelLayout.STEREO)

    def test_sound_only_binding_plays_sound(self) -> None:
        bank = SoundBank(
            sounds=(_tone(),),
            bindings=(
                EventBinding(
                    id="b1",
                    event_type=EventType.CELL_CREATED.value,
                    sound_id="ding",
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(len(self.sink.buffers), 1)

    def test_voice_and_sound_mix_into_one_buffer(self) -> None:
        bank = SoundBank(
            voices=(_voice(),),
            sounds=(_tone(),),
            bindings=(
                EventBinding(
                    id="b1",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    sound_id="ding",
                    say_template="hi",
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(len(self.sink.buffers), 1)

    def test_predicate_gate_skips_non_matching_event(self) -> None:
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b1",
                    event_type=EventType.COMMAND_COMPLETED.value,
                    voice_id="narrator",
                    say_template="completed",
                    predicate="exit_code == 0",
                ),
                EventBinding(
                    id="b2",
                    event_type=EventType.COMMAND_COMPLETED.value,
                    voice_id="narrator",
                    say_template="failed",
                    predicate="exit_code != 0",
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(
            self.bus,
            EventType.COMMAND_COMPLETED,
            {"cell_id": "c", "exit_code": 0, "timed_out": False},
            source="kernel",
        )
        self.assertEqual(len(self.sink.buffers), 1)

    def test_template_renders_with_payload(self) -> None:
        spoken: list[str] = []

        class RecordingTTS:
            sample_rate = 8000

            def synthesize(self, text, voice):
                spoken.append(text)
                from asat.audio import AudioBuffer
                return AudioBuffer.mono([0.0] * 16, 8000)

        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="new cell {cell_id}",
                ),
            ),
        )
        engine = SoundEngine(
            self.bus,
            bank,
            self.sink,
            tts=RecordingTTS(),
            sample_rate=8000,
        )
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {"cell_id": "abc"}, source="notebook")
        self.assertEqual(spoken, ["new cell abc"])

    def test_template_missing_key_renders_blank(self) -> None:
        spoken: list[str] = []

        class RecordingTTS:
            sample_rate = 8000

            def synthesize(self, text, voice):
                spoken.append(text)
                from asat.audio import AudioBuffer
                return AudioBuffer.mono([0.0] * 16, 8000)

        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="prefix {missing} suffix",
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, tts=RecordingTTS(), sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(spoken, ["prefix  suffix"])

    def test_bindings_run_in_priority_order(self) -> None:
        invocations: list[str] = []

        class RecordingTTS:
            sample_rate = 8000

            def synthesize(self, text, voice):
                invocations.append(text)
                from asat.audio import AudioBuffer
                return AudioBuffer.mono([0.0] * 16, 8000)

        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="low",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="low",
                    priority=10,
                ),
                EventBinding(
                    id="high",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="high",
                    priority=500,
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, tts=RecordingTTS(), sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(invocations, ["high", "low"])

    def test_disabled_binding_is_silent(self) -> None:
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="off",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="hush",
                    enabled=False,
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(len(self.sink.buffers), 0)

    def test_audio_spoken_is_published(self) -> None:
        seen = []

        def capture(event):
            seen.append(event)

        self.bus.subscribe(EventType.AUDIO_SPOKEN, capture)

        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="hi",
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["binding_id"], "b")
        self.assertEqual(seen[0].payload["text"], "hi")

    def test_engine_does_not_react_to_its_own_events(self) -> None:
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="loop",
                    event_type=EventType.AUDIO_SPOKEN.value,
                    voice_id="narrator",
                    say_template="echo",
                ),
                EventBinding(
                    id="root",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="root",
                ),
            ),
        )
        engine = SoundEngine(self.bus, bank, self.sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(self.bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(len(self.sink.buffers), 1)


class BindingOverrideTests(unittest.TestCase):
    """Per-binding voice / sound overrides reshape the record on render."""

    def test_voice_overrides_alter_profile_passed_to_tts(self) -> None:
        bus = EventBus()
        tts = _RecordingTTS(sample_rate=8000)
        sink = MemorySink()
        voice = _voice(pitch=1.0, rate=1.0, volume=1.0, azimuth=0.0)
        binding = EventBinding(
            id="b1",
            event_type=EventType.CELL_CREATED.value,
            voice_id="narrator",
            say_template="hi",
            voice_overrides={"pitch": 0.5, "azimuth": -60.0},
        )
        bank = SoundBank(voices=(voice,), bindings=(binding,))
        engine = SoundEngine(bus, bank, sink, tts=tts, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(bus, EventType.CELL_CREATED, {"cell_id": "c1"}, source="notebook")

        self.assertEqual(len(tts.calls), 1)
        profile = tts.calls[0]
        # pitch multiplier 0.5 against default 140Hz baseline => 70Hz
        self.assertAlmostEqual(profile.pitch_hz, 70.0, places=3)
        self.assertAlmostEqual(profile.position.azimuth_degrees, -60.0, places=3)

    def test_no_overrides_passes_voice_parameters_verbatim(self) -> None:
        bus = EventBus()
        tts = _RecordingTTS(sample_rate=8000)
        sink = MemorySink()
        voice = _voice(pitch=1.2, azimuth=15.0)
        binding = EventBinding(
            id="b1",
            event_type=EventType.CELL_CREATED.value,
            voice_id="narrator",
            say_template="hi",
        )
        bank = SoundBank(voices=(voice,), bindings=(binding,))
        engine = SoundEngine(bus, bank, sink, tts=tts, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(bus, EventType.CELL_CREATED, {}, source="notebook")

        profile = tts.calls[0]
        self.assertAlmostEqual(profile.pitch_hz, 140.0 * 1.2, places=3)
        self.assertAlmostEqual(profile.position.azimuth_degrees, 15.0, places=3)

    def test_sound_overrides_change_played_volume(self) -> None:
        bus = EventBus()
        sink = MemorySink()
        recipe = _tone(volume=1.0)
        loud_binding = EventBinding(
            id="loud",
            event_type=EventType.CELL_CREATED.value,
            sound_id="ding",
        )
        quiet_binding = EventBinding(
            id="quiet",
            event_type=EventType.CELL_UPDATED.value,
            sound_id="ding",
            sound_overrides={"volume": 0.1},
        )
        bank = SoundBank(
            sounds=(recipe,), bindings=(loud_binding, quiet_binding)
        )
        engine = SoundEngine(bus, bank, sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(bus, EventType.CELL_CREATED, {}, source="notebook")
        publish_event(bus, EventType.CELL_UPDATED, {}, source="notebook")

        self.assertEqual(len(sink.buffers), 2)
        loud_peak = max(abs(sample) for sample in sink.buffers[0].samples)
        quiet_peak = max(abs(sample) for sample in sink.buffers[1].samples)
        self.assertGreater(loud_peak, quiet_peak * 2)


class SoundEngineLifecycleTests(unittest.TestCase):

    def test_close_unsubscribes(self) -> None:
        bus = EventBus()
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="x",
                ),
            ),
        )
        engine = SoundEngine(bus, bank, MemorySink(), sample_rate=8000)
        self.assertEqual(bus.subscriber_count(EventType.CELL_CREATED), 1)
        engine.close()
        self.assertEqual(bus.subscriber_count(EventType.CELL_CREATED), 0)

    def test_set_bank_swaps_subscriptions(self) -> None:
        bus = EventBus()
        initial = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="x",
                ),
            ),
        )
        engine = SoundEngine(bus, initial, MemorySink(), sample_rate=8000)
        self.addCleanup(engine.close)

        replacement = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.COMMAND_COMPLETED.value,
                    voice_id="narrator",
                    say_template="done",
                ),
            ),
        )
        engine.set_bank(replacement)

        self.assertEqual(bus.subscriber_count(EventType.CELL_CREATED), 0)
        self.assertEqual(bus.subscriber_count(EventType.COMMAND_COMPLETED), 1)

    def test_unknown_event_type_is_ignored(self) -> None:
        bus = EventBus()
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type="something.made.up",
                    voice_id="narrator",
                    say_template="ignored",
                ),
            ),
        )
        engine = SoundEngine(bus, bank, MemorySink(), sample_rate=8000)
        self.addCleanup(engine.close)
        self.assertEqual(len(engine._subscribed_types), 0)  # type: ignore[attr-defined]

    def test_bank_with_dangling_voice_reference_rejected_at_load(self) -> None:
        bus = EventBus()
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="ghost",
                    say_template="hi",
                ),
            ),
        )
        from asat.sound_bank import SoundBankError

        with self.assertRaises(SoundBankError):
            SoundEngine(bus, bank, MemorySink(), sample_rate=8000)


class SourceFilteringTests(unittest.TestCase):

    def test_events_from_engine_source_are_ignored(self) -> None:
        bus = EventBus()
        sink = MemorySink()
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="x",
                ),
            ),
        )
        engine = SoundEngine(bus, bank, sink, sample_rate=8000)
        self.addCleanup(engine.close)

        publish_event(bus, EventType.CELL_CREATED, {}, source=SoundEngine.SOURCE)
        self.assertEqual(len(sink.buffers), 0)


class NarrationHistoryTests(unittest.TestCase):
    """F30: ring buffer + `replay_last_narration` on SoundEngine."""

    def _engine(self, *, history_capacity: int = 20) -> tuple[SoundEngine, MemorySink, EventBus]:
        bus = EventBus()
        sink = MemorySink()
        bank = SoundBank(
            voices=(_voice(),),
            bindings=(
                EventBinding(
                    id="b_cell",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    say_template="new cell {cell_id}",
                ),
                EventBinding(
                    id="b_focus",
                    event_type=EventType.FOCUS_CHANGED.value,
                    voice_id="narrator",
                    say_template="now in {new_mode}",
                ),
            ),
        )
        engine = SoundEngine(
            bus, bank, sink, sample_rate=8000, history_capacity=history_capacity
        )
        self.addCleanup(engine.close)
        return engine, sink, bus

    def test_spoken_phrases_are_recorded_in_history(self) -> None:
        engine, _sink, bus = self._engine()
        publish_event(bus, EventType.CELL_CREATED, {"cell_id": "c1"}, source="notebook")
        publish_event(bus, EventType.CELL_CREATED, {"cell_id": "c2"}, source="notebook")
        history = engine.narration_history
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1].text, "new cell c2")
        self.assertEqual(history[-1].voice_id, "narrator")
        self.assertEqual(history[-1].binding_id, "b_cell")
        self.assertEqual(history[-1].event_type, "cell.created")

    def test_empty_templates_are_not_recorded(self) -> None:
        # A sound-only binding with no voice/text shouldn't land in
        # the narration history — there's nothing to repeat.
        bus = EventBus()
        sink = MemorySink()
        bank = SoundBank(
            sounds=(_tone(),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.CELL_CREATED.value,
                    sound_id="ding",
                ),
            ),
        )
        engine = SoundEngine(bus, bank, sink, sample_rate=8000)
        self.addCleanup(engine.close)
        publish_event(bus, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(engine.narration_history, ())

    def test_history_capacity_caps_growth(self) -> None:
        engine, _sink, bus = self._engine(history_capacity=3)
        for index in range(5):
            publish_event(
                bus, EventType.CELL_CREATED, {"cell_id": f"c{index}"}, source="notebook"
            )
        history = engine.narration_history
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].text, "new cell c2")
        self.assertEqual(history[-1].text, "new cell c4")

    def test_replay_last_narration_empty_history_returns_none(self) -> None:
        engine, sink, _bus = self._engine()
        self.assertIsNone(engine.replay_last_narration())
        self.assertEqual(sink.buffers, ())

    def test_replay_last_narration_replays_via_same_voice(self) -> None:
        engine, sink, bus = self._engine()
        replayed: list = []
        bus.subscribe(EventType.NARRATION_REPLAYED, replayed.append)
        publish_event(bus, EventType.CELL_CREATED, {"cell_id": "c1"}, source="notebook")
        buffers_before = len(sink.buffers)
        entry = engine.replay_last_narration()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.text, "new cell c1")
        self.assertEqual(len(sink.buffers), buffers_before + 1)
        self.assertEqual(len(replayed), 1)
        payload = replayed[0].payload
        self.assertEqual(payload["text"], "new cell c1")
        self.assertEqual(payload["voice_id"], "narrator")
        self.assertEqual(payload["binding_id"], "b_cell")
        self.assertEqual(payload["event_type"], "cell.created")

    def test_replay_last_does_not_recurse_into_history(self) -> None:
        # The replay plays a pre-recorded text directly through TTS;
        # it must not re-enter the bindings path and double the buffer.
        engine, _sink, bus = self._engine()
        publish_event(bus, EventType.CELL_CREATED, {"cell_id": "c1"}, source="notebook")
        history_before = engine.narration_history
        engine.replay_last_narration()
        self.assertEqual(engine.narration_history, history_before)

    def test_replay_skips_when_voice_was_removed(self) -> None:
        # If the user swapped banks between speaking and replaying
        # and the new bank has no matching voice, replay should
        # gracefully no-op instead of crashing.
        engine, sink, bus = self._engine()
        publish_event(bus, EventType.CELL_CREATED, {"cell_id": "c1"}, source="notebook")
        bare = SoundBank()
        engine.set_bank(bare)
        self.assertIsNone(engine.replay_last_narration())
        # No new buffer, no NARRATION_REPLAYED.
        self.assertEqual(len(sink.buffers), 1)


if __name__ == "__main__":
    unittest.main()
