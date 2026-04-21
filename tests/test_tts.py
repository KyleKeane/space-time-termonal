"""Unit tests for the ToneTTSEngine."""

from __future__ import annotations

import unittest

from asat.audio import AudioBuffer, ChannelLayout, VoiceProfile
from asat.tts import ToneTTSEngine


class ToneTTSBasicTests(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = ToneTTSEngine(sample_rate=8000)
        self.voice = VoiceProfile.stdout_default()

    def test_synthesizes_mono_audio_buffer(self) -> None:
        buffer = self.engine.synthesize("hi", self.voice)
        self.assertIsInstance(buffer, AudioBuffer)
        self.assertEqual(buffer.layout, ChannelLayout.MONO)
        self.assertEqual(buffer.sample_rate, 8000)

    def test_empty_text_returns_short_silence(self) -> None:
        buffer = self.engine.synthesize("", self.voice)
        self.assertTrue(buffer.is_mono())
        self.assertTrue(all(sample == 0.0 for sample in buffer.samples))

    def test_whitespace_text_returns_short_silence(self) -> None:
        buffer = self.engine.synthesize("   ", self.voice)
        self.assertTrue(all(sample == 0.0 for sample in buffer.samples))

    def test_longer_text_produces_longer_buffer(self) -> None:
        short = self.engine.synthesize("a", self.voice)
        long = self.engine.synthesize("abcdef", self.voice)
        self.assertGreater(long.frame_count(), short.frame_count())

    def test_output_is_deterministic(self) -> None:
        first = self.engine.synthesize("hello", self.voice)
        second = self.engine.synthesize("hello", self.voice)
        self.assertEqual(first.samples, second.samples)

    def test_different_text_produces_different_output(self) -> None:
        a_buf = self.engine.synthesize("aaaa", self.voice)
        b_buf = self.engine.synthesize("bbbb", self.voice)
        self.assertNotEqual(a_buf.samples, b_buf.samples)


class ToneTTSVoiceTests(unittest.TestCase):

    def test_volume_scales_amplitude(self) -> None:
        engine = ToneTTSEngine(sample_rate=8000)
        loud = VoiceProfile(
            name="loud",
            pitch_hz=200.0,
            speed_wpm=200.0,
            volume=1.0,
            position=VoiceProfile.stdout_default().position,
        )
        quiet = VoiceProfile(
            name="quiet",
            pitch_hz=200.0,
            speed_wpm=200.0,
            volume=0.25,
            position=VoiceProfile.stdout_default().position,
        )
        loud_buf = engine.synthesize("a", loud)
        quiet_buf = engine.synthesize("a", quiet)
        self.assertAlmostEqual(
            max(abs(sample) for sample in loud_buf.samples),
            4 * max(abs(sample) for sample in quiet_buf.samples),
            places=5,
        )

    def test_speed_affects_duration(self) -> None:
        engine = ToneTTSEngine(sample_rate=8000)
        slow = VoiceProfile(
            name="slow",
            pitch_hz=200.0,
            speed_wpm=100.0,
            volume=0.5,
            position=VoiceProfile.stdout_default().position,
        )
        fast = VoiceProfile(
            name="fast",
            pitch_hz=200.0,
            speed_wpm=400.0,
            volume=0.5,
            position=VoiceProfile.stdout_default().position,
        )
        slow_buf = engine.synthesize("abcd", slow)
        fast_buf = engine.synthesize("abcd", fast)
        self.assertGreater(slow_buf.frame_count(), fast_buf.frame_count())


class Pyttsx3ConfigRejectionTests(unittest.TestCase):
    """Verify that property rejections are recorded, not silently dropped.

    Previously ``_try_set`` did ``except Exception: pass`` — a user
    adjusting pitch and hearing no change had no way to tell whether
    their backend accepted the value. Now each rejection is appended
    to a bounded ring on the engine instance and exposed via the
    ``config_rejections`` property.
    """

    def test_rejected_property_is_recorded_not_silently_dropped(self) -> None:
        from asat.tts import Pyttsx3Engine

        # Don't construct the real backend; we just want to probe
        # _try_set in isolation. Build the engine directly and call
        # its method with a backend that raises.
        engine = Pyttsx3Engine(sample_rate=22050)

        class _BrokenBackend:
            def setProperty(self, key, value):
                raise RuntimeError(f"driver does not support {key!r}")

        engine._try_set(_BrokenBackend(), "pitch", 1.5)

        rejections = engine.config_rejections
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]["property"], "pitch")
        self.assertEqual(rejections[0]["value"], "1.5")
        self.assertIn("RuntimeError", rejections[0]["error"])
        self.assertIn("pitch", rejections[0]["error"])

    def test_rejection_ring_is_capped_at_16(self) -> None:
        from asat.tts import Pyttsx3Engine

        engine = Pyttsx3Engine(sample_rate=22050)

        class _BrokenBackend:
            def setProperty(self, key, value):
                raise RuntimeError("always fails")

        backend = _BrokenBackend()
        for i in range(50):
            engine._try_set(backend, f"prop_{i}", i)

        rejections = engine.config_rejections
        self.assertEqual(len(rejections), 16)
        # The ring keeps the most recent entries, not the oldest.
        self.assertEqual(rejections[-1]["property"], "prop_49")
        self.assertEqual(rejections[0]["property"], "prop_34")


if __name__ == "__main__":
    unittest.main()
