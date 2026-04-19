"""Audio sinks: where finished buffers go.

An AudioSink receives fully-prepared AudioBuffer values and either
plays them, stores them, or writes them to disk. Keeping the sink
behind a narrow protocol means tests can substitute a recording sink
without spinning up audio hardware, and future playback backends
(sounddevice, an ALSA wrapper, a Windows WASAPI wrapper) plug in the
same way.

The shipping sinks are:

- MemorySink accumulates every buffer it receives. Tests use it to
  verify the pipeline's output directly, and it is the CLI default so
  `python -m asat` always starts cleanly on any OS.
- WavFileSink writes each buffer to disk as 16-bit PCM. Useful for
  debugging and for listening to the output when no audio device is
  available.
- WindowsLiveAudioSink plays buffers through `winsound.PlaySound` on
  Windows. It is the first sink that produces actual audio on real
  speakers; call `pick_live_sink()` from the CLI to select it on the
  target platform.

POSIX live playback is still on the roadmap (see
docs/FEATURE_REQUESTS.md, F6). `pick_live_sink()` raises
`LiveAudioUnavailable` on non-Windows hosts so the CLI can fall back
to `MemorySink` with a clear message.
"""

from __future__ import annotations

import io
import struct
import sys
import wave
from pathlib import Path
from typing import Protocol

from asat.audio import AudioBuffer, ChannelLayout


class AudioSink(Protocol):
    """Final stage of the audio pipeline.

    Implementations in-tree: ``MemorySink`` (tests), ``WavFileSink``
    (``--wav-dir`` CLI mode), ``WindowsLiveAudioSink`` (``--live`` on
    Windows). Pluggable: add a new sink by implementing ``play`` and
    ``close`` with the same signatures.
    """

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
    packed = _pcm16_bytes(buffer)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(buffer.sample_rate)
        writer.writeframes(packed)


def buffer_to_wav_bytes(buffer: AudioBuffer) -> bytes:
    """Return a full in-memory WAV file (header + PCM) for the given buffer.

    Live sinks that accept a complete WAV blob — notably Windows'
    `winsound.PlaySound` with `SND_MEMORY` — use this helper so the
    file layout matches what `write_wav` produces on disk.
    """
    channels = 2 if buffer.layout == ChannelLayout.STEREO else 1
    packed = _pcm16_bytes(buffer)
    with io.BytesIO() as memory:
        with wave.open(memory, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(buffer.sample_rate)
            writer.writeframes(packed)
        return memory.getvalue()


def _pcm16_bytes(buffer: AudioBuffer) -> bytes:
    """Quantise `buffer.samples` to little-endian signed 16-bit PCM."""
    clamped = [_clamp(value) for value in buffer.samples]
    int_samples = [int(value * 32767) for value in clamped]
    return struct.pack("<" + "h" * len(int_samples), *int_samples)


def _clamp(value: float) -> float:
    """Clamp a sample into the range [-1.0, 1.0] before quantization."""
    if value > 1.0:
        return 1.0
    if value < -1.0:
        return -1.0
    return value


class LiveAudioUnavailable(RuntimeError):
    """Raised when a live speaker sink cannot be constructed on this host.

    The CLI catches this, prints a friendly explanation, and falls back
    to `MemorySink` so the session still starts instead of crashing.
    """


class WindowsLiveAudioSink:
    """Play each buffer through `winsound.PlaySound`.

    Each `play()` builds a tiny in-memory WAV and calls `PlaySound`
    with `SND_MEMORY | SND_ASYNC`. The flags mean:

    - `SND_MEMORY`: data is the WAV bytes themselves, not a filename.
    - `SND_ASYNC`: return immediately; the OS mixes playback on its
      own thread. The next `play()` will interrupt any previous
      asynchronous clip that is still going, which matches the
      "latest event wins" behaviour we want for keystroke feedback.

    The sink imports `winsound` lazily so this module still loads on
    POSIX hosts (where the rest of the repo continues to work against
    `MemorySink` / `WavFileSink`).
    """

    def __init__(self) -> None:
        """Import winsound or raise `LiveAudioUnavailable`."""
        try:
            import winsound  # noqa: F401  # imported for availability check
        except ImportError as exc:
            raise LiveAudioUnavailable(
                "winsound is only available on Windows",
            ) from exc
        self._winsound = __import__("winsound")

    def play(self, buffer: AudioBuffer) -> None:
        """Render the buffer to a WAV blob and hand it to PlaySound."""
        data = buffer_to_wav_bytes(buffer)
        flags = self._winsound.SND_MEMORY | self._winsound.SND_ASYNC
        self._winsound.PlaySound(data, flags)

    def close(self) -> None:
        """Stop any in-flight playback so the process exits quietly."""
        self._winsound.PlaySound(None, self._winsound.SND_PURGE)


def pick_live_sink() -> AudioSink:
    """Return the live sink that fits this host, or raise.

    Today only Windows has a live implementation. POSIX hosts get a
    clear `LiveAudioUnavailable` so the CLI can tell the user what to
    do (use `--wav-dir DIR` today; live POSIX is F6).
    """
    if sys.platform.startswith("win"):
        return WindowsLiveAudioSink()
    raise LiveAudioUnavailable(
        "no live audio sink is available on this platform yet — "
        "use --wav-dir DIR to capture the output as WAV files "
        "(tracked as FEATURE_REQUESTS.md F6)",
    )
