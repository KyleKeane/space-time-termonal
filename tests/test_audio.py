"""Unit tests for the audio data types."""

from __future__ import annotations

import unittest

from asat.audio import (
    AudioBuffer,
    ChannelLayout,
    DEFAULT_SAMPLE_RATE,
    SpatialPosition,
    VoicePreset,
    VoiceProfile,
)


class AudioBufferMonoTests(unittest.TestCase):

    def test_mono_factory_preserves_samples(self) -> None:
        buffer = AudioBuffer.mono([0.1, -0.2, 0.3])
        self.assertEqual(buffer.layout, ChannelLayout.MONO)
        self.assertEqual(buffer.samples, (0.1, -0.2, 0.3))
        self.assertEqual(buffer.sample_rate, DEFAULT_SAMPLE_RATE)

    def test_mono_frame_count_and_duration(self) -> None:
        buffer = AudioBuffer.mono([0.0] * 22050, sample_rate=22050)
        self.assertEqual(buffer.frame_count(), 22050)
        self.assertAlmostEqual(buffer.duration_seconds(), 1.0)

    def test_silence_is_mono_by_default_and_zero(self) -> None:
        buffer = AudioBuffer.silence(0.1)
        self.assertTrue(buffer.is_mono())
        self.assertTrue(all(sample == 0.0 for sample in buffer.samples))

    def test_left_channel_requires_stereo(self) -> None:
        buffer = AudioBuffer.mono([0.1, 0.2])
        with self.assertRaises(ValueError):
            buffer.left_channel()


class AudioBufferStereoTests(unittest.TestCase):

    def test_stereo_interleaves_channels(self) -> None:
        buffer = AudioBuffer.stereo([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0])
        self.assertEqual(buffer.layout, ChannelLayout.STEREO)
        self.assertEqual(buffer.samples, (1.0, -1.0, 2.0, -2.0, 3.0, -3.0))

    def test_stereo_channel_accessors(self) -> None:
        buffer = AudioBuffer.stereo([0.1, 0.2], [0.5, 0.6])
        self.assertEqual(buffer.left_channel(), (0.1, 0.2))
        self.assertEqual(buffer.right_channel(), (0.5, 0.6))

    def test_stereo_frame_count_halves_sample_count(self) -> None:
        buffer = AudioBuffer.stereo([0.0] * 5, [0.0] * 5, sample_rate=1000)
        self.assertEqual(buffer.frame_count(), 5)
        self.assertEqual(len(buffer.samples), 10)

    def test_stereo_mismatched_channels_raises(self) -> None:
        with self.assertRaises(ValueError):
            AudioBuffer.stereo([0.1, 0.2, 0.3], [0.4, 0.5])


class VoiceProfileTests(unittest.TestCase):

    def test_stdout_profile_is_slightly_left(self) -> None:
        profile = VoiceProfile.stdout_default()
        self.assertEqual(profile.name, VoicePreset.STDOUT.value)
        self.assertLess(profile.position.azimuth_degrees, 0.0)

    def test_stderr_profile_is_right_of_center(self) -> None:
        profile = VoiceProfile.stderr_default()
        self.assertEqual(profile.name, VoicePreset.STDERR.value)
        self.assertGreater(profile.position.azimuth_degrees, 0.0)

    def test_notification_profile_is_overhead(self) -> None:
        profile = VoiceProfile.notification_default()
        self.assertGreater(profile.position.elevation_degrees, 0.0)

    def test_profile_is_frozen(self) -> None:
        profile = VoiceProfile.stdout_default()
        with self.assertRaises(Exception):
            profile.volume = 0.1  # type: ignore[misc]


class SpatialPositionTests(unittest.TestCase):

    def test_defaults_are_center(self) -> None:
        position = SpatialPosition()
        self.assertEqual(position.azimuth_degrees, 0.0)
        self.assertEqual(position.elevation_degrees, 0.0)
        self.assertEqual(position.distance_meters, 1.0)


if __name__ == "__main__":
    unittest.main()
