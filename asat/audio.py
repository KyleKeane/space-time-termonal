"""Core audio data types.

Everything in the audio pipeline passes data as AudioBuffer values.
Mono buffers leave the TTS engine and enter the spatializer; stereo
buffers leave the spatializer and reach the audio sink. The buffers
themselves are immutable tuples of Python floats in the range
[-1.0, 1.0]. Keeping them frozen makes reasoning about who owns a
buffer trivial: nobody can mutate it in place.

VoiceProfile and SpatialPosition describe how a piece of text should
sound and where in the stereo field it should appear. The defaults on
each class match the spatial layout described in the project spec:
stdout slightly to the left, stderr slightly to the right, system
notifications overhead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


DEFAULT_SAMPLE_RATE = 22050


class ChannelLayout(str, Enum):
    """Number and arrangement of channels in an AudioBuffer."""

    MONO = "mono"
    STEREO = "stereo"


@dataclass(frozen=True)
class AudioBuffer:
    """Immutable container for PCM samples.

    samples: tuple of floats in [-1.0, 1.0]. For stereo, samples are
        interleaved left, right, left, right, ...
    sample_rate: samples per second per channel.
    layout: mono or stereo.
    """

    samples: tuple[float, ...]
    sample_rate: int
    layout: ChannelLayout

    @classmethod
    def mono(cls, samples: Iterable[float], sample_rate: int = DEFAULT_SAMPLE_RATE) -> "AudioBuffer":
        """Build a mono buffer from an iterable of float samples."""
        return cls(tuple(float(s) for s in samples), sample_rate, ChannelLayout.MONO)

    @classmethod
    def stereo(
        cls,
        left: Iterable[float],
        right: Iterable[float],
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> "AudioBuffer":
        """Build a stereo buffer from separate left and right channels.

        Raises ValueError if the two channels differ in length. They
        must be equal because the output is interleaved and must align
        sample for sample.
        """
        left_tuple = tuple(float(s) for s in left)
        right_tuple = tuple(float(s) for s in right)
        if len(left_tuple) != len(right_tuple):
            raise ValueError("Left and right channels must be the same length")
        interleaved: list[float] = []
        for l_sample, r_sample in zip(left_tuple, right_tuple):
            interleaved.append(l_sample)
            interleaved.append(r_sample)
        return cls(tuple(interleaved), sample_rate, ChannelLayout.STEREO)

    @classmethod
    def silence(
        cls,
        duration_seconds: float,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        layout: ChannelLayout = ChannelLayout.MONO,
    ) -> "AudioBuffer":
        """Return a silent buffer of the given duration and layout."""
        frame_count = max(0, int(duration_seconds * sample_rate))
        sample_count = frame_count * (2 if layout == ChannelLayout.STEREO else 1)
        return cls(tuple(0.0 for _ in range(sample_count)), sample_rate, layout)

    def frame_count(self) -> int:
        """Return the number of audio frames (time steps) in the buffer."""
        divisor = 2 if self.layout == ChannelLayout.STEREO else 1
        return len(self.samples) // divisor

    def duration_seconds(self) -> float:
        """Return the playback duration of the buffer in seconds."""
        if self.sample_rate <= 0:
            return 0.0
        return self.frame_count() / self.sample_rate

    def is_mono(self) -> bool:
        """Return True if the buffer has one channel."""
        return self.layout == ChannelLayout.MONO

    def is_stereo(self) -> bool:
        """Return True if the buffer has two interleaved channels."""
        return self.layout == ChannelLayout.STEREO

    def left_channel(self) -> tuple[float, ...]:
        """Return the left channel of a stereo buffer."""
        self._require_stereo()
        return self.samples[0::2]

    def right_channel(self) -> tuple[float, ...]:
        """Return the right channel of a stereo buffer."""
        self._require_stereo()
        return self.samples[1::2]

    def _require_stereo(self) -> None:
        """Raise ValueError unless the buffer is stereo."""
        if not self.is_stereo():
            raise ValueError("Operation is only defined for stereo buffers")


@dataclass(frozen=True)
class SpatialPosition:
    """Direction from which a sound should appear to originate.

    azimuth_degrees: horizontal angle. 0 is directly ahead, positive
        values rotate clockwise (right), negative counter-clockwise
        (left). Wraps in [-180, 180].
    elevation_degrees: vertical angle. 0 is ear level, 90 is directly
        overhead, -90 is directly below.
    distance_meters: perceived distance. Used by future attenuation
        logic; the Phase 3 synthetic HRTF ignores it.
    """

    azimuth_degrees: float = 0.0
    elevation_degrees: float = 0.0
    distance_meters: float = 1.0


class VoicePreset(str, Enum):
    """Named voice profiles the engine uses out of the box."""

    STDOUT = "stdout"
    STDERR = "stderr"
    NOTIFICATION = "notification"
    USER_INPUT = "user_input"


@dataclass(frozen=True)
class VoiceProfile:
    """Synthesis and spatialization parameters for one voice.

    name: human-readable identifier, also matched by VoicePreset.
    pitch_hz: base fundamental frequency the TTS engine should target.
    speed_wpm: target words per minute.
    volume: linear gain in [0.0, 1.0] applied after synthesis.
    position: where in the stereo field the voice appears.
    metadata: free-form dictionary for backend-specific options (e.g.,
        an espeak voice name, an SSML override).
    """

    name: str
    pitch_hz: float
    speed_wpm: float
    volume: float
    position: SpatialPosition
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def stdout_default(cls) -> "VoiceProfile":
        """Voice for standard output: calm tone, slightly left of center."""
        return cls(
            name=VoicePreset.STDOUT.value,
            pitch_hz=130.0,
            speed_wpm=220.0,
            volume=0.85,
            position=SpatialPosition(azimuth_degrees=-20.0),
        )

    @classmethod
    def stderr_default(cls) -> "VoiceProfile":
        """Voice for standard error: higher pitch, slightly right of center."""
        return cls(
            name=VoicePreset.STDERR.value,
            pitch_hz=180.0,
            speed_wpm=200.0,
            volume=0.95,
            position=SpatialPosition(azimuth_degrees=35.0, elevation_degrees=-10.0),
        )

    @classmethod
    def notification_default(cls) -> "VoiceProfile":
        """Voice for system notifications: overhead, attention-grabbing."""
        return cls(
            name=VoicePreset.NOTIFICATION.value,
            pitch_hz=160.0,
            speed_wpm=190.0,
            volume=0.9,
            position=SpatialPosition(azimuth_degrees=0.0, elevation_degrees=70.0),
        )

    @classmethod
    def user_input_default(cls) -> "VoiceProfile":
        """Voice for echoing user input: centered, front."""
        return cls(
            name=VoicePreset.USER_INPUT.value,
            pitch_hz=140.0,
            speed_wpm=230.0,
            volume=0.8,
            position=SpatialPosition(azimuth_degrees=0.0),
        )
