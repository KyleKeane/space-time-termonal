"""Unit tests for HRTFProfile, Spatializer, and convolution."""

from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

from asat.audio import AudioBuffer, SpatialPosition
from asat.hrtf import HRTFProfile, Spatializer, convolve


def _unit_impulse_stereo_wav(path: Path, left_tap: int, right_tap: int, length: int = 32) -> None:
    """Write a stereo WAV whose channels are unit impulses at given taps."""
    samples: list[int] = []
    for i in range(length):
        left = 32767 if i == left_tap else 0
        right = 32767 if i == right_tap else 0
        samples.append(left)
        samples.append(right)
    packed = struct.pack("<" + "h" * len(samples), *samples)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(packed)


class ConvolveTests(unittest.TestCase):

    def test_convolution_with_unit_impulse_is_identity(self) -> None:
        signal = [1.0, 2.0, 3.0]
        kernel = [1.0]
        self.assertEqual(convolve(signal, kernel), (1.0, 2.0, 3.0))

    def test_delayed_impulse_shifts_signal(self) -> None:
        signal = [1.0, 0.0, 0.0]
        kernel = [0.0, 0.0, 1.0]
        result = convolve(signal, kernel)
        self.assertEqual(result, (0.0, 0.0, 1.0, 0.0, 0.0))

    def test_empty_inputs_return_empty(self) -> None:
        self.assertEqual(convolve([], [1.0]), ())
        self.assertEqual(convolve([1.0], []), ())

    def test_length_is_n_plus_m_minus_one(self) -> None:
        signal = [0.5, 0.5, 0.5, 0.5]
        kernel = [1.0, 1.0]
        result = convolve(signal, kernel)
        self.assertEqual(len(result), len(signal) + len(kernel) - 1)


class HRTFProfileValidationTests(unittest.TestCase):

    def test_mismatched_irs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HRTFProfile(left_ir=(1.0, 0.0), right_ir=(1.0,), sample_rate=8000)

    def test_empty_irs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HRTFProfile(left_ir=(), right_ir=(), sample_rate=8000)

    def test_nonpositive_sample_rate_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HRTFProfile(left_ir=(1.0,), right_ir=(1.0,), sample_rate=0)


class HRTFSyntheticTests(unittest.TestCase):

    def test_centered_position_is_symmetric(self) -> None:
        profile = HRTFProfile.synthetic(SpatialPosition(), sample_rate=8000, length=16)
        self.assertEqual(profile.left_ir, profile.right_ir)

    def test_right_position_delays_and_attenuates_left_ear(self) -> None:
        profile = HRTFProfile.synthetic(
            SpatialPosition(azimuth_degrees=90.0), sample_rate=8000, length=32
        )
        first_left_tap = next((i for i, value in enumerate(profile.left_ir) if value != 0.0), -1)
        first_right_tap = next((i for i, value in enumerate(profile.right_ir) if value != 0.0), -1)
        self.assertEqual(first_right_tap, 0)
        self.assertGreater(first_left_tap, 0)
        self.assertLess(max(profile.left_ir), max(profile.right_ir))

    def test_left_position_delays_and_attenuates_right_ear(self) -> None:
        profile = HRTFProfile.synthetic(
            SpatialPosition(azimuth_degrees=-90.0), sample_rate=8000, length=32
        )
        first_left_tap = next((i for i, value in enumerate(profile.left_ir) if value != 0.0), -1)
        first_right_tap = next((i for i, value in enumerate(profile.right_ir) if value != 0.0), -1)
        self.assertEqual(first_left_tap, 0)
        self.assertGreater(first_right_tap, 0)


class HRTFWavLoadingTests(unittest.TestCase):

    def test_round_trip_from_stereo_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir.wav"
            _unit_impulse_stereo_wav(path, left_tap=0, right_tap=5)
            profile = HRTFProfile.from_stereo_wav(path)
        self.assertEqual(profile.sample_rate, 8000)
        self.assertGreater(profile.left_ir[0], 0.99)
        self.assertGreater(profile.right_ir[5], 0.99)


class SpatializerTests(unittest.TestCase):

    def test_mono_becomes_stereo(self) -> None:
        mono = AudioBuffer.mono([1.0, 0.0, 0.0], sample_rate=8000)
        profile = HRTFProfile.synthetic(SpatialPosition(azimuth_degrees=45.0), sample_rate=8000, length=8)
        result = Spatializer().spatialize(mono, profile)
        self.assertTrue(result.is_stereo())
        self.assertEqual(result.sample_rate, 8000)
        self.assertEqual(result.frame_count(), len(mono.samples) + profile.length() - 1)

    def test_right_biased_position_favors_right_channel(self) -> None:
        mono = AudioBuffer.mono([0.5] * 64, sample_rate=8000)
        profile = HRTFProfile.synthetic(
            SpatialPosition(azimuth_degrees=60.0), sample_rate=8000, length=16
        )
        result = Spatializer().spatialize(mono, profile)
        left_energy = sum(sample * sample for sample in result.left_channel())
        right_energy = sum(sample * sample for sample in result.right_channel())
        self.assertGreater(right_energy, left_energy)

    def test_sample_rate_mismatch_rejected(self) -> None:
        mono = AudioBuffer.mono([0.1, 0.2], sample_rate=16000)
        profile = HRTFProfile.synthetic(SpatialPosition(), sample_rate=8000, length=8)
        with self.assertRaises(ValueError):
            Spatializer().spatialize(mono, profile)

    def test_stereo_input_rejected(self) -> None:
        stereo = AudioBuffer.stereo([0.1, 0.2], [0.3, 0.4], sample_rate=8000)
        profile = HRTFProfile.synthetic(SpatialPosition(), sample_rate=8000, length=4)
        with self.assertRaises(ValueError):
            Spatializer().spatialize(stereo, profile)


if __name__ == "__main__":
    unittest.main()
