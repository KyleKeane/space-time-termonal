"""Unit tests for the sound generator layer."""

from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

from asat.audio import DEFAULT_SAMPLE_RATE, ChannelLayout
from asat.sound_bank import SoundRecipe
from asat.sound_generators import (
    ChordGenerator,
    SampleGenerator,
    SilenceGenerator,
    SoundGenerator,
    SoundGeneratorError,
    SoundGeneratorRegistry,
    ToneGenerator,
    WAVEFORMS,
    generate_sound,
)


def _write_wav(path: Path, samples: list[float], sample_rate: int = 22050, channels: int = 1) -> None:
    """Write a tiny WAV file so SampleGenerator tests have input."""
    int_samples = [max(-32768, min(32767, int(value * 32767))) for value in samples]
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(struct.pack("<" + "h" * len(int_samples), *int_samples))


class ToneGeneratorTests(unittest.TestCase):

    def test_sine_tone_matches_requested_duration(self) -> None:
        recipe = SoundRecipe(id="s", kind="tone", params={"frequency": 440.0, "duration": 0.1})
        buffer = ToneGenerator().generate(recipe, sample_rate=10000)
        self.assertEqual(buffer.layout, ChannelLayout.MONO)
        self.assertEqual(buffer.frame_count(), 1000)
        self.assertAlmostEqual(buffer.duration_seconds(), 0.1, places=6)

    def test_tone_respects_recipe_volume(self) -> None:
        loud = SoundRecipe(id="a", kind="tone", params={"frequency": 440.0, "duration": 0.05}, volume=1.0)
        soft = SoundRecipe(id="b", kind="tone", params={"frequency": 440.0, "duration": 0.05}, volume=0.25)
        loud_peak = max(abs(s) for s in ToneGenerator().generate(loud, sample_rate=10000).samples)
        soft_peak = max(abs(s) for s in ToneGenerator().generate(soft, sample_rate=10000).samples)
        self.assertGreater(loud_peak, soft_peak)
        self.assertAlmostEqual(soft_peak / loud_peak, 0.25, places=2)

    def test_all_declared_waveforms_produce_samples(self) -> None:
        for waveform in WAVEFORMS:
            recipe = SoundRecipe(
                id=f"t_{waveform}",
                kind="tone",
                params={"frequency": 220.0, "duration": 0.05, "waveform": waveform, "attack": 0.0, "release": 0.0},
            )
            buffer = ToneGenerator().generate(recipe, sample_rate=4000)
            self.assertGreater(max(abs(s) for s in buffer.samples), 0.0)

    def test_envelope_fades_in_and_out(self) -> None:
        recipe = SoundRecipe(
            id="fade",
            kind="tone",
            params={"frequency": 440.0, "duration": 0.2, "attack": 0.05, "release": 0.05},
        )
        samples = ToneGenerator().generate(recipe, sample_rate=10000).samples
        self.assertAlmostEqual(samples[0], 0.0, places=6)
        mid = abs(samples[len(samples) // 2])
        self.assertGreater(mid, abs(samples[0]))
        self.assertGreater(mid, abs(samples[-1]))

    def test_rejects_missing_frequency(self) -> None:
        with self.assertRaises(SoundGeneratorError):
            ToneGenerator().generate(SoundRecipe(id="x", kind="tone"), sample_rate=10000)

    def test_rejects_non_tone_kind(self) -> None:
        with self.assertRaises(SoundGeneratorError):
            ToneGenerator().generate(
                SoundRecipe(id="x", kind="silence", params={"duration": 0.1}),
                sample_rate=10000,
            )

    def test_rejects_unknown_waveform(self) -> None:
        recipe = SoundRecipe(
            id="x",
            kind="tone",
            params={"frequency": 440.0, "waveform": "banana"},
        )
        with self.assertRaises(SoundGeneratorError):
            ToneGenerator().generate(recipe, sample_rate=10000)


class ChordGeneratorTests(unittest.TestCase):

    def test_chord_has_expected_duration(self) -> None:
        recipe = SoundRecipe(
            id="c",
            kind="chord",
            params={"frequencies": [440.0, 554.37, 659.25], "duration": 0.1},
        )
        buffer = ChordGenerator().generate(recipe, sample_rate=10000)
        self.assertEqual(buffer.frame_count(), 1000)

    def test_chord_stays_in_range_when_many_partials(self) -> None:
        recipe = SoundRecipe(
            id="big",
            kind="chord",
            params={"frequencies": [220.0, 330.0, 440.0, 550.0, 660.0], "duration": 0.1, "attack": 0.0, "release": 0.0},
        )
        buffer = ChordGenerator().generate(recipe, sample_rate=10000)
        self.assertLessEqual(max(abs(s) for s in buffer.samples), 1.0001)

    def test_chord_rejects_empty_frequencies(self) -> None:
        recipe = SoundRecipe(id="c", kind="chord", params={"frequencies": []})
        with self.assertRaises(SoundGeneratorError):
            ChordGenerator().generate(recipe, sample_rate=10000)

    def test_chord_rejects_missing_frequencies(self) -> None:
        with self.assertRaises(SoundGeneratorError):
            ChordGenerator().generate(SoundRecipe(id="c", kind="chord"), sample_rate=10000)


class SilenceGeneratorTests(unittest.TestCase):

    def test_silence_has_zero_peak(self) -> None:
        recipe = SoundRecipe(id="s", kind="silence", params={"duration": 0.05})
        buffer = SilenceGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.layout, ChannelLayout.MONO)
        self.assertEqual(buffer.frame_count(), 400)
        self.assertEqual(max(abs(s) for s in buffer.samples), 0.0)

    def test_zero_duration_silence_is_empty_but_valid(self) -> None:
        recipe = SoundRecipe(id="s", kind="silence", params={"duration": 0.0})
        buffer = SilenceGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.frame_count(), 0)


class SampleGeneratorTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def test_sample_loads_mono_wav(self) -> None:
        path = self.tmpdir / "mono.wav"
        _write_wav(path, [0.5, -0.5, 0.25, -0.25], sample_rate=8000, channels=1)
        recipe = SoundRecipe(id="s", kind="sample", params={"path": str(path)})
        buffer = SampleGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.layout, ChannelLayout.MONO)
        self.assertEqual(buffer.frame_count(), 4)
        self.assertAlmostEqual(buffer.samples[0], 0.5, places=3)

    def test_sample_downmixes_stereo_by_averaging(self) -> None:
        path = self.tmpdir / "stereo.wav"
        _write_wav(path, [0.5, 0.0, 0.5, 0.0], sample_rate=8000, channels=2)
        recipe = SoundRecipe(id="s", kind="sample", params={"path": str(path)})
        buffer = SampleGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.frame_count(), 2)
        self.assertAlmostEqual(buffer.samples[0], 0.25, places=3)

    def test_sample_resamples_to_target_rate(self) -> None:
        path = self.tmpdir / "slow.wav"
        _write_wav(path, [0.1] * 80, sample_rate=4000, channels=1)
        recipe = SoundRecipe(id="s", kind="sample", params={"path": str(path)})
        buffer = SampleGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.sample_rate, 8000)
        self.assertAlmostEqual(buffer.duration_seconds(), 0.02, places=3)

    def test_sample_trims_start_and_end(self) -> None:
        path = self.tmpdir / "trim.wav"
        _write_wav(path, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], sample_rate=8000)
        recipe = SoundRecipe(
            id="s",
            kind="sample",
            params={"path": str(path), "start": 2 / 8000, "end": 5 / 8000},
        )
        buffer = SampleGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.frame_count(), 3)

    def test_sample_loops_to_target_duration(self) -> None:
        path = self.tmpdir / "tiny.wav"
        _write_wav(path, [0.1, 0.2], sample_rate=8000, channels=1)
        recipe = SoundRecipe(
            id="s",
            kind="sample",
            params={"path": str(path), "loop": True, "duration": 8 / 8000},
        )
        buffer = SampleGenerator().generate(recipe, sample_rate=8000)
        self.assertEqual(buffer.frame_count(), 8)

    def test_sample_missing_path_is_error(self) -> None:
        recipe = SoundRecipe(id="s", kind="sample", params={})
        with self.assertRaises(SoundGeneratorError):
            SampleGenerator().generate(recipe, sample_rate=8000)

    def test_sample_nonexistent_path_is_error(self) -> None:
        recipe = SoundRecipe(id="s", kind="sample", params={"path": str(self.tmpdir / "ghost.wav")})
        with self.assertRaises(SoundGeneratorError):
            SampleGenerator().generate(recipe, sample_rate=8000)


class RegistryTests(unittest.TestCase):

    def test_default_registry_covers_every_sound_kind(self) -> None:
        from asat.sound_bank import SOUND_KINDS

        registry = SoundGeneratorRegistry.default()
        for kind in SOUND_KINDS:
            self.assertIsNotNone(registry.generator_for(kind))

    def test_unknown_kind_raises(self) -> None:
        registry = SoundGeneratorRegistry()
        with self.assertRaises(SoundGeneratorError):
            registry.generator_for("banana")

    def test_register_overrides_existing_entry(self) -> None:
        calls: list[str] = []

        class Dummy:
            def generate(self, recipe: SoundRecipe, *, sample_rate: int):
                calls.append(recipe.id)
                return SilenceGenerator().generate(
                    SoundRecipe(id=recipe.id, kind="silence", params={"duration": 0.0}),
                    sample_rate=sample_rate,
                )

        registry = SoundGeneratorRegistry.default()
        registry.register("tone", Dummy())
        recipe = SoundRecipe(id="x", kind="tone", params={"frequency": 440.0})
        registry.generate(recipe, sample_rate=8000)
        self.assertEqual(calls, ["x"])

    def test_generate_sound_uses_default_registry(self) -> None:
        recipe = SoundRecipe(id="s", kind="silence", params={"duration": 0.01})
        buffer = generate_sound(recipe, sample_rate=8000)
        self.assertEqual(buffer.frame_count(), 80)

    def test_generate_sound_uses_supplied_registry(self) -> None:
        registry = SoundGeneratorRegistry()
        registry.register("silence", SilenceGenerator())
        recipe = SoundRecipe(id="s", kind="silence", params={"duration": 0.01})
        buffer = generate_sound(recipe, sample_rate=8000, registry=registry)
        self.assertEqual(buffer.frame_count(), 80)


class ProtocolTests(unittest.TestCase):

    def test_every_generator_satisfies_protocol(self) -> None:
        generators: list[SoundGenerator] = [
            ToneGenerator(),
            ChordGenerator(),
            SampleGenerator(),
            SilenceGenerator(),
        ]
        for generator in generators:
            self.assertTrue(hasattr(generator, "generate"))


class DefaultSampleRateTests(unittest.TestCase):

    def test_default_matches_audio_module(self) -> None:
        recipe = SoundRecipe(id="s", kind="silence", params={"duration": 0.0})
        self.assertEqual(
            generate_sound(recipe).sample_rate,
            DEFAULT_SAMPLE_RATE,
        )


if __name__ == "__main__":
    unittest.main()
