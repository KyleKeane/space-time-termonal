"""HRTF loading and spatialization via convolution.

An HRTFProfile is a pair of impulse responses, one for each ear,
captured (or synthesized) for a specific direction of arrival. The
Spatializer convolves a mono TTS buffer with the chosen profile to
produce a stereo buffer the listener will perceive as coming from
that direction.

Two ways to obtain a profile:

- HRTFProfile.from_stereo_wav(path) reads a stereo WAV whose left and
  right channels are the two impulse responses. This is the format
  most HRIR datasets use when converted from SOFA.
- HRTFProfile.synthetic(position) synthesizes a trivial
  interaural-time-difference and interaural-level-difference pair
  from a SpatialPosition. It is not accurate enough for production
  but is exactly what the project spec asks for in Phase 3: a dummy
  HRTF file sufficient to prove the spatialization pipeline.

Convolution is implemented in pure Python and will transparently use
numpy.convolve if numpy is importable. No hard numpy dependency.
"""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from asat.audio import (
    AudioBuffer,
    ChannelLayout,
    DEFAULT_SAMPLE_RATE,
    SpatialPosition,
)


SPEED_OF_SOUND_MPS = 343.0
HEAD_RADIUS_METERS = 0.0875


@dataclass(frozen=True)
class HRTFProfile:
    """A pair of head-related impulse responses for one direction.

    left_ir and right_ir are equal-length tuples of floats that will
    be convolved with a mono signal to produce the left and right ear
    outputs respectively.
    """

    left_ir: tuple[float, ...]
    right_ir: tuple[float, ...]
    sample_rate: int

    def __post_init__(self) -> None:
        """Validate the profile on construction."""
        if len(self.left_ir) != len(self.right_ir):
            raise ValueError("Left and right impulse responses must have equal length")
        if not self.left_ir:
            raise ValueError("Impulse responses must not be empty")
        if self.sample_rate <= 0:
            raise ValueError("Sample rate must be positive")

    def length(self) -> int:
        """Return the length of each impulse response in samples."""
        return len(self.left_ir)

    @classmethod
    def from_stereo_wav(cls, path: Path | str) -> "HRTFProfile":
        """Load a stereo WAV whose channels are the two impulse responses."""
        with wave.open(str(path), "rb") as reader:
            if reader.getnchannels() != 2:
                raise ValueError("HRTF WAV file must be stereo")
            if reader.getsampwidth() != 2:
                raise ValueError("HRTF WAV file must be 16-bit PCM")
            sample_rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
        left, right = _deinterleave_s16le(frames)
        return cls(left_ir=left, right_ir=right, sample_rate=sample_rate)

    @classmethod
    def synthetic(
        cls,
        position: SpatialPosition,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        length: int = 64,
    ) -> "HRTFProfile":
        """Synthesize a delay-plus-gain HRTF for the given position.

        The model is intentionally minimal but directional:
        - The contralateral ear is delayed by the interaural time
          difference implied by the azimuth.
        - The contralateral ear is attenuated by a level factor
          derived from the azimuth (head shadow approximation).
        This is enough to produce audibly different left/right and
        front/back cues for tests and demos. It is not a substitute
        for a measured HRTF in production use.
        """
        azimuth_radians = math.radians(position.azimuth_degrees)
        itd_seconds = _interaural_time_difference(azimuth_radians)
        delay_samples = int(round(abs(itd_seconds) * sample_rate))
        level_close, level_far = _interaural_level_factors(azimuth_radians)
        if position.azimuth_degrees >= 0:
            left_ir = _impulse(length, delay_samples, level_far)
            right_ir = _impulse(length, 0, level_close)
        else:
            left_ir = _impulse(length, 0, level_close)
            right_ir = _impulse(length, delay_samples, level_far)
        return cls(left_ir=left_ir, right_ir=right_ir, sample_rate=sample_rate)


class Spatializer:
    """Applies an HRTFProfile to a mono AudioBuffer to produce stereo."""

    def spatialize(self, mono: AudioBuffer, profile: HRTFProfile) -> AudioBuffer:
        """Convolve the mono buffer with the profile and return stereo.

        Raises ValueError if the buffer is not mono or if sample rates
        disagree. The output length is len(mono) + len(ir) - 1 frames.
        """
        if not mono.is_mono():
            raise ValueError("Spatializer input must be a mono buffer")
        if mono.sample_rate != profile.sample_rate:
            raise ValueError("Mono buffer and HRTF profile must share a sample rate")
        left = convolve(mono.samples, profile.left_ir)
        right = convolve(mono.samples, profile.right_ir)
        return AudioBuffer.stereo(left, right, mono.sample_rate)


def convolve(signal: Sequence[float], kernel: Sequence[float]) -> tuple[float, ...]:
    """Return the full linear convolution of signal with kernel.

    Uses numpy.convolve if numpy is importable, which is typical of
    HRTF-sized kernels in production. Otherwise falls back to a pure
    Python implementation so the library continues to function with
    only the standard library installed.
    """
    try:
        import numpy as np  # noqa: PLC0415

        result = np.convolve(np.asarray(signal, dtype=float), np.asarray(kernel, dtype=float))
        return tuple(float(value) for value in result)
    except ImportError:
        return _convolve_python(signal, kernel)


def _convolve_python(signal: Sequence[float], kernel: Sequence[float]) -> tuple[float, ...]:
    """Pure Python fallback convolution for environments without numpy."""
    n, m = len(signal), len(kernel)
    if n == 0 or m == 0:
        return ()
    out = [0.0] * (n + m - 1)
    for i, s in enumerate(signal):
        if s == 0.0:
            continue
        for j, k in enumerate(kernel):
            out[i + j] += s * k
    return tuple(out)


def _impulse(length: int, delay: int, gain: float) -> tuple[float, ...]:
    """Build a sparse impulse of the given length with one nonzero tap."""
    safe_delay = max(0, min(delay, length - 1))
    values = [0.0] * length
    values[safe_delay] = gain
    return tuple(values)


def _interaural_time_difference(azimuth_radians: float) -> float:
    """Return the ITD in seconds for a given azimuth, Woodworth model."""
    return (HEAD_RADIUS_METERS / SPEED_OF_SOUND_MPS) * (azimuth_radians + math.sin(azimuth_radians))


def _interaural_level_factors(azimuth_radians: float) -> tuple[float, float]:
    """Return (close_ear_gain, far_ear_gain) for the given azimuth.

    Close ear keeps full gain; far ear is attenuated as azimuth grows.
    Maximum attenuation is roughly -6 dB at +/- 90 degrees.
    """
    attenuation = 0.5 + 0.5 * math.cos(azimuth_radians)
    return 1.0, max(0.25, attenuation)


def _deinterleave_s16le(frames: bytes) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Split signed-16-bit little-endian interleaved stereo frames by channel."""
    sample_count = len(frames) // 2
    values = struct.unpack("<" + "h" * sample_count, frames)
    left = tuple(values[i] / 32768.0 for i in range(0, sample_count, 2))
    right = tuple(values[i] / 32768.0 for i in range(1, sample_count, 2))
    return left, right
