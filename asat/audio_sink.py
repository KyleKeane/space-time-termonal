"""Audio sinks: where finished buffers go.

An AudioSink receives fully-prepared AudioBuffer values and either
plays them, stores them, or writes them to disk. Keeping the sink
behind a narrow protocol means tests can substitute a recording sink
without spinning up audio hardware, and future playback backends
(sounddevice, an ALSA wrapper, a Windows WASAPI wrapper) plug in the
same way.

Phase 3 ships two sinks:

- MemorySink accumulates every buffer it receives. Tests use it to
  verify the pipeline's output directly.
- WavFileSink writes each buffer to disk as 16-bit PCM. Useful for
  debugging and for listening to the output when no audio device is
  available in the current environment.

A live speaker sink will be added in a later phase when the audio
hardware dependency is in scope.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import Protocol

from asat.audio import AudioBuffer, ChannelLayout


class AudioSink(Protocol):
    """Final stage of the audio pipeline."""

    def play(self, buffer: AudioBuffer) -> None:
        """Deliver a fully-rendered audio buffer to the sink."""
        ...

    def close(self) -> None:
        """Release any underlying resources the sink holds."""
        ...


class MemorySink:
    """Record every buffer passed to play, for tests and introspection."""

    def __init__(self) -> None:
        """Initialize an empty recording."""
        self._buffers: list[AudioBuffer] = []

    def play(self, buffer: AudioBuffer) -> None:
        """Append the buffer to the internal record."""
        self._buffers.append(buffer)

    def close(self) -> None:
        """No-op. Present to satisfy the AudioSink protocol."""

    @property
    def buffers(self) -> tuple[AudioBuffer, ...]:
        """Return the sequence of buffers received so far."""
        return tuple(self._buffers)

    def reset(self) -> None:
        """Clear the recording. Useful between test scenarios."""
        self._buffers.clear()


class WavFileSink:
    """Write each buffer to a numbered WAV file in the target directory.

    The sink creates one file per play call so individual utterances
    remain easy to inspect. All files use 16-bit PCM, matching what
    most audio tools consume. The caller is responsible for creating
    the target directory beforehand if it does not already exist.
    """

    def __init__(self, directory: Path | str, prefix: str = "utterance") -> None:
        """Remember where files will be written and their name prefix."""
        self._directory = Path(directory)
        self._prefix = prefix
        self._counter = 0
        self._written: list[Path] = []

    def play(self, buffer: AudioBuffer) -> None:
        """Write the buffer to the next numbered file in the directory."""
        self._counter += 1
        filename = f"{self._prefix}-{self._counter:04d}.wav"
        path = self._directory / filename
        write_wav(path, buffer)
        self._written.append(path)

    def close(self) -> None:
        """No-op. Files are closed by write_wav as they are produced."""

    @property
    def written_files(self) -> tuple[Path, ...]:
        """Return the paths of every file this sink has produced."""
        return tuple(self._written)


def write_wav(path: Path | str, buffer: AudioBuffer) -> None:
    """Write the given buffer to a 16-bit PCM WAV file at path."""
    channels = 2 if buffer.layout == ChannelLayout.STEREO else 1
    clamped = [_clamp(value) for value in buffer.samples]
    int_samples = [int(value * 32767) for value in clamped]
    packed = struct.pack("<" + "h" * len(int_samples), *int_samples)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(buffer.sample_rate)
        writer.writeframes(packed)


def _clamp(value: float) -> float:
    """Clamp a sample into the range [-1.0, 1.0] before quantization."""
    if value > 1.0:
        return 1.0
    if value < -1.0:
        return -1.0
    return value
