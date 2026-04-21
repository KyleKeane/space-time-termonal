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
    _apply_gain,
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

    def test_conjunction_requires_all_clauses_to_match(self) -> None:
        payload = {"transition": "cell", "kind": "heading"}
        self.assertTrue(
            self.evaluator.matches(
                "transition == 'cell' and kind == 'heading'", payload
            )
        )
        self.assertFalse(
            self.evaluator.matches(
                "transition == 'cell' and kind == 'heading'",
                {"transition": "cell", "kind": "command"},
            )
        )
        self.assertFalse(
            self.evaluator.matches(
                "transition == 'cell' and kind == 'heading'",
                {"transition": "mode", "kind": "heading"},
            )
        )


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


class _SilentTTS:
    """TTS shim that always returns mono silence of a fixed length.

    Used by the ducking tests so the speech contribution is zero and
    every sample the sink sees is sourced from the cue alone — that
    lets us assert duck_level was applied by comparing the cue's peak
    amplitude against the same engine run with ducking disabled.
    """

    def __init__(self, sample_rate: int = 8000, frames: int = 64) -> None:
        self.sample_rate = sample_rate
        self._frames = frames

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        return AudioBuffer.mono([0.0] * self._frames, self.sample_rate)


class _ApplyGainTests(unittest.TestCase):
    """F32 — _apply_gain scales every sample, keeps metadata intact."""

    def test_scales_every_sample(self) -> None:
        buffer = AudioBuffer.mono([0.5, -0.5, 1.0, -1.0], sample_rate=8000)
        scaled = _apply_gain(buffer, 0.5)
        self.assertEqual(scaled.samples, (0.25, -0.25, 0.5, -0.5))
        self.assertEqual(scaled.sample_rate, 8000)
        self.assertEqual(scaled.layout, ChannelLayout.MONO)

    def test_unity_gain_returns_input_buffer(self) -> None:
        # Hot-path optimisation: identical instance, no tuple churn.
        buffer = AudioBuffer.mono([0.1, 0.2, 0.3], sample_rate=8000)
        self.assertIs(_apply_gain(buffer, 1.0), buffer)

    def test_zero_gain_silences_buffer(self) -> None:
        buffer = AudioBuffer.mono([0.5, -0.5, 1.0], sample_rate=8000)
        silenced = _apply_gain(buffer, 0.0)
        self.assertEqual(silenced.samples, (0.0, 0.0, 0.0))


class SoundEngineDuckingTests(unittest.TestCase):
    """F32 — concurrent cues are attenuated while speech is mixing."""

    def _bank(self, *, ducking_enabled: bool, duck_level: float) -> SoundBank:
        return SoundBank(
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
            ducking_enabled=ducking_enabled,
            duck_level=duck_level,
        )

    def _peak(self, sink: MemorySink) -> float:
        self.assertEqual(len(sink.buffers), 1)
        return max(abs(sample) for sample in sink.buffers[0].samples)

    def test_ducking_attenuates_concurrent_cue(self) -> None:
        # Run the same dispatch twice, once with ducking off and once
        # with duck_level 0.25; the attenuated peak must be smaller.
        bus_off, sink_off = EventBus(), MemorySink()
        engine_off = SoundEngine(
            bus_off,
            self._bank(ducking_enabled=False, duck_level=0.25),
            sink_off,
            tts=_SilentTTS(),
            sample_rate=8000,
        )
        self.addCleanup(engine_off.close)
        publish_event(bus_off, EventType.CELL_CREATED, {}, source="notebook")
        peak_off = self._peak(sink_off)

        bus_on, sink_on = EventBus(), MemorySink()
        engine_on = SoundEngine(
            bus_on,
            self._bank(ducking_enabled=True, duck_level=0.25),
            sink_on,
            tts=_SilentTTS(),
            sample_rate=8000,
        )
        self.addCleanup(engine_on.close)
        publish_event(bus_on, EventType.CELL_CREATED, {}, source="notebook")
        peak_on = self._peak(sink_on)

        # Speech is silent in both runs, so the only contribution to
        # the mix is the cue. With the silent-speech short-circuit at
        # gain 1.0 disabled, the ducked peak should be ~25% of the
        # full-level one — allow a small numerical fudge for the
        # spatializer's HRTF convolution.
        self.assertGreater(peak_off, 0.0)
        self.assertLess(peak_on, peak_off * 0.5)

    def test_disabled_ducking_leaves_cue_untouched(self) -> None:
        bus = EventBus()
        sink = MemorySink()
        engine = SoundEngine(
            bus,
            self._bank(ducking_enabled=False, duck_level=0.0),
            sink,
            tts=_SilentTTS(),
            sample_rate=8000,
        )
        self.addCleanup(engine.close)
        publish_event(bus, EventType.CELL_CREATED, {}, source="notebook")
        # duck_level is 0.0 but ducking is OFF, so the cue must still
        # be audible — the disabled flag wins over the level value.
        self.assertGreater(self._peak(sink), 0.0)

    def test_ducking_skipped_when_no_speech_buffer(self) -> None:
        # A sound-only binding has no speech to duck against, so the
        # cue must come through at full level even with ducking on.
        bank = SoundBank(
            sounds=(_tone(),),
            bindings=(
                EventBinding(
                    id="b1",
                    event_type=EventType.CELL_CREATED.value,
                    sound_id="ding",
                ),
            ),
            ducking_enabled=True,
            duck_level=0.1,
        )
        bus = EventBus()
        sink = MemorySink()
        engine = SoundEngine(bus, bank, sink, sample_rate=8000)
        self.addCleanup(engine.close)
        publish_event(bus, EventType.CELL_CREATED, {}, source="notebook")
        peak_solo = self._peak(sink)

        # Same cue without ducking should match — confirms the ducking
        # branch was never entered.
        bus2 = EventBus()
        sink2 = MemorySink()
        engine2 = SoundEngine(
            bus2,
            SoundBank(
                sounds=(_tone(),),
                bindings=bank.bindings,
                ducking_enabled=False,
                duck_level=0.1,
            ),
            sink2,
            sample_rate=8000,
        )
        self.addCleanup(engine2.close)
        publish_event(bus2, EventType.CELL_CREATED, {}, source="notebook")
        self.assertEqual(peak_solo, self._peak(sink2))


class SoundEngineVerbosityTests(unittest.TestCase):
    """F31 — bank-level verbosity ceiling filters bindings at dispatch."""

    def _tiered_bank(self, level: str) -> SoundBank:
        return SoundBank(
            voices=(_voice(),),
            sounds=(_tone(),),
            bindings=(
                EventBinding(
                    id="critical",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    sound_id="ding",
                    say_template="boom",
                    verbosity="minimal",
                ),
                EventBinding(
                    id="chatty",
                    event_type=EventType.CELL_CREATED.value,
                    voice_id="narrator",
                    sound_id="ding",
                    say_template="details",
                    verbosity="verbose",
                ),
            ),
            verbosity_level=level,
        )

    def _spoken_binding_ids(self, bus: EventBus, sink: MemorySink) -> list[str]:
        recorded: list[str] = []
        bus.subscribe(
            EventType.AUDIO_SPOKEN,
            lambda event: recorded.append(str(event.payload.get("binding_id"))),
        )
        publish_event(bus, EventType.CELL_CREATED, {}, source="notebook")
        return recorded

    def test_minimal_level_silences_verbose_bindings(self) -> None:
        bus, sink = EventBus(), MemorySink()
        engine = SoundEngine(bus, self._tiered_bank("minimal"), sink, sample_rate=8000)
        self.addCleanup(engine.close)
        fired = self._spoken_binding_ids(bus, sink)
        self.assertEqual(fired, ["critical"])

    def test_verbose_level_plays_every_tier(self) -> None:
        bus, sink = EventBus(), MemorySink()
        engine = SoundEngine(bus, self._tiered_bank("verbose"), sink, sample_rate=8000)
        self.addCleanup(engine.close)
        fired = sorted(self._spoken_binding_ids(bus, sink))
        self.assertEqual(fired, ["chatty", "critical"])

    def test_set_verbosity_level_swaps_bank_and_publishes_event(self) -> None:
        bus, sink = EventBus(), MemorySink()
        engine = SoundEngine(bus, self._tiered_bank("verbose"), sink, sample_rate=8000)
        self.addCleanup(engine.close)
        observed: list[dict] = []
        bus.subscribe(
            EventType.VERBOSITY_CHANGED,
            lambda event: observed.append(dict(event.payload)),
        )
        engine.set_verbosity_level("minimal")
        self.assertEqual(engine.bank.verbosity_level, "minimal")
        self.assertEqual(observed, [{"level": "minimal", "previous": "verbose"}])

        # Re-setting to the current level is a no-op and publishes nothing.
        engine.set_verbosity_level("minimal")
        self.assertEqual(len(observed), 1)

    def test_set_verbosity_level_rejects_unknown(self) -> None:
        from asat.sound_bank import SoundBankError

        bus, sink = EventBus(), MemorySink()
        engine = SoundEngine(bus, self._tiered_bank("normal"), sink, sample_rate=8000)
        self.addCleanup(engine.close)
        with self.assertRaises(SoundBankError):
            engine.set_verbosity_level("whisper")


class _FailingTTS:
    """TTS stand-in that raises on demand, for reliability tests."""

    def __init__(self, fail_next: bool = False) -> None:
        self.fail_next = fail_next
        self._inner = ToneTTSEngine(sample_rate=8000)
        self.call_count = 0

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        self.call_count += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("TTS backend simulated crash")
        return self._inner.synthesize(text, voice)


class _FailingSink:
    """Sink stand-in that can fail on demand, for reliability tests."""

    def __init__(self) -> None:
        self.played: list[AudioBuffer] = []
        self.fail_count = 0  # how many more plays should raise

    def play(self, buffer: AudioBuffer) -> None:
        if self.fail_count > 0:
            self.fail_count -= 1
            raise OSError("sink simulated device failure")
        self.played.append(buffer)

    def close(self) -> None:
        pass


class SoundEngineReliabilityTests(unittest.TestCase):
    """Verify the Never Crashes invariant at the SoundEngine layer."""

    def _bank_with_binding(self) -> SoundBank:
        voice = _voice(id="narrator")
        binding = EventBinding(
            id="command_submitted_default",
            event_type=EventType.COMMAND_SUBMITTED.value,
            voice_id=voice.id,
            say_template="ran {cell_id}",
            priority=100,
        )
        return SoundBank(voices=(voice,), sounds=(), bindings=(binding,))

    def test_tts_exception_does_not_propagate_and_fires_failure_event(self) -> None:
        # Never Crashes: a TTS that raises must not take the dispatch
        # down. The error tone must reach the sink, and
        # AUDIO_PIPELINE_FAILED must be published so downstream tools
        # (event log, settings editor) can surface the failure.
        bus = EventBus()
        sink = MemorySink()
        tts = _FailingTTS(fail_next=True)
        observed_failures: list[dict] = []
        bus.subscribe(
            EventType.AUDIO_PIPELINE_FAILED,
            lambda event: observed_failures.append(dict(event.payload)),
        )
        engine = SoundEngine(
            bus,
            self._bank_with_binding(),
            sink,
            tts=tts,
            sample_rate=8000,
        )
        self.addCleanup(engine.close)

        # Must not raise — the Never Crashes invariant.
        publish_event(
            bus,
            EventType.COMMAND_SUBMITTED,
            {"cell_id": "c1", "command": "ls"},
            source="test",
        )

        self.assertEqual(len(observed_failures), 1)
        self.assertEqual(observed_failures[0]["event_type"], "command.submitted")
        self.assertEqual(observed_failures[0]["error_class"], "RuntimeError")
        self.assertIn("simulated crash", observed_failures[0]["error_message"])
        # The error tone must have reached the sink so the user is
        # not left in silence.
        self.assertEqual(len(sink.buffers), 1)
        # The error tone is mono at the engine's sample rate.
        self.assertEqual(sink.buffers[0].sample_rate, 8000)
        self.assertEqual(sink.buffers[0].layout, ChannelLayout.MONO)

    def test_subsequent_events_still_render_after_a_failure(self) -> None:
        # After one failing render, a later successful render must
        # still produce audio normally.
        bus = EventBus()
        sink = MemorySink()
        tts = _FailingTTS(fail_next=True)
        engine = SoundEngine(
            bus,
            self._bank_with_binding(),
            sink,
            tts=tts,
            sample_rate=8000,
        )
        self.addCleanup(engine.close)

        publish_event(
            bus,
            EventType.COMMAND_SUBMITTED,
            {"cell_id": "c1", "command": "ls"},
            source="test",
        )
        # The next event hits the same path but the TTS recovered
        # (fail_next auto-resets to False inside synthesize).
        publish_event(
            bus,
            EventType.COMMAND_SUBMITTED,
            {"cell_id": "c2", "command": "pwd"},
            source="test",
        )

        # Two sink plays: the error tone from the first event, then
        # the successful speech buffer from the second event.
        self.assertEqual(len(sink.buffers), 2)

    def test_sink_swaps_to_memory_after_three_consecutive_failures(self) -> None:
        # Never Crashes + graceful degrade: after the live sink has
        # refused 3 buffers in a row, SoundEngine must swap it for
        # MemorySink so subsequent events stop trying a broken device.
        bus = EventBus()
        sink = _FailingSink()
        sink.fail_count = 100  # always fail
        tts = _FailingTTS(fail_next=False)
        observed_degrade: list[dict] = []
        bus.subscribe(
            EventType.AUDIO_SINK_DEGRADED,
            lambda event: observed_degrade.append(dict(event.payload)),
        )
        engine = SoundEngine(
            bus,
            self._bank_with_binding(),
            sink,
            tts=tts,
            sample_rate=8000,
        )
        self.addCleanup(engine.close)

        # Force three failures by making TTS raise each time; the
        # error tone tries to reach the sink and is rejected.
        for i in range(3):
            tts.fail_next = True
            publish_event(
                bus,
                EventType.COMMAND_SUBMITTED,
                {"cell_id": f"c{i}", "command": "ls"},
                source="test",
            )

        self.assertEqual(len(observed_degrade), 1)
        self.assertEqual(observed_degrade[0]["previous_sink"], "_FailingSink")
        self.assertIn("MemorySink", observed_degrade[0]["reason"])

    def test_failure_in_failure_handler_does_not_recurse(self) -> None:
        # Re-entrancy guard: if publishing AUDIO_PIPELINE_FAILED
        # itself triggers a failure (e.g. a pathological handler),
        # the dispatch still returns cleanly instead of infinite
        # recursion.
        bus = EventBus()
        sink = MemorySink()
        tts = _FailingTTS(fail_next=True)
        # A handler that itself raises whenever AUDIO_PIPELINE_FAILED
        # is published. SoundEngine's guard must absorb this too.
        bus.subscribe(
            EventType.AUDIO_PIPELINE_FAILED,
            lambda event: (_ for _ in ()).throw(ValueError("handler boom")),
        )
        engine = SoundEngine(
            bus,
            self._bank_with_binding(),
            sink,
            tts=tts,
            sample_rate=8000,
        )
        self.addCleanup(engine.close)

        # Must not raise despite the nested failure.
        publish_event(
            bus,
            EventType.COMMAND_SUBMITTED,
            {"cell_id": "c1", "command": "ls"},
            source="test",
        )


if __name__ == "__main__":
    unittest.main()
