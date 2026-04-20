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
  Windows.
- PosixLiveAudioSink pipes each buffer as a WAV blob to a local
  audio player binary — ``aplay`` / ``paplay`` on Linux, ``afplay``
  on macOS — so ``pick_live_sink()`` returns a working live sink on
  every supported platform (F6). Install one of the binaries with
  your package manager; PulseAudio, PipeWire, and ALSA are all fine
  so long as a command-line player is on PATH.

``pick_live_sink()`` tries these in order: Windows first (existing
``winsound`` path), then ``PosixLiveAudioSink.probe()`` which picks
whichever POSIX player is available. Only when none are installable
does it raise ``LiveAudioUnavailable`` — and even then the CLI falls
back to ``MemorySink`` with a message that names the player binaries
to install.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import Optional, Protocol

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


class PosixLiveAudioSink:
    """Play each buffer through a local audio player subprocess.

    POSIX live audio (F6) routes through whatever command-line player
    the host has installed. The sink probes a small list in priority
    order and uses the first one it finds:

    * ``paplay`` — PulseAudio / PipeWire's native player, common on
      modern Linux desktops.
    * ``aplay`` — ALSA's stock player, present on essentially every
      Linux install.
    * ``afplay`` — macOS' built-in player (requires Darwin).

    ``play`` spawns one ``Popen`` per buffer, streams the in-memory
    WAV bytes in on stdin, and does NOT block: audio mixes on the
    player's own thread. A new ``play`` call kills the previous
    process so the latest cue always wins over a still-going clip —
    the same "latest event wins" semantics as the Windows sink.

    Use :class:`pick_live_sink` to construct one; direct construction
    raises :class:`LiveAudioUnavailable` when no player is present so
    callers never end up with a zombie sink that silently drops every
    buffer.
    """

    DEFAULT_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("paplay", ()),
        ("aplay", ("-q",)),  # -q silences aplay's per-buffer header chatter.
        ("afplay", ()),
    )

    def __init__(
        self,
        *,
        binary: Optional[str] = None,
        extra_args: tuple[str, ...] = (),
        candidates: Optional[tuple[tuple[str, tuple[str, ...]], ...]] = None,
    ) -> None:
        """Probe for a player binary and remember how to invoke it.

        ``binary`` pins a specific player (e.g. ``"aplay"``); when
        ``None`` we walk ``candidates`` (default: pulse, alsa, afplay)
        and pick the first one on PATH. Raises ``LiveAudioUnavailable``
        when nothing matches so the CLI can fall back cleanly.
        """
        probe_list = candidates if candidates is not None else self.DEFAULT_CANDIDATES
        if binary is not None:
            resolved = shutil.which(binary)
            if resolved is None:
                raise LiveAudioUnavailable(
                    f"requested live-audio binary {binary!r} is not on PATH"
                )
            self._binary = resolved
            self._args: tuple[str, ...] = extra_args
        else:
            pick = self._probe(probe_list)
            if pick is None:
                names = ", ".join(name for name, _ in probe_list)
                raise LiveAudioUnavailable(
                    f"no POSIX live-audio player found on PATH "
                    f"(tried: {names}). Install one via your package manager — "
                    "for example `apt install pulseaudio-utils` or "
                    "`apt install alsa-utils` on Linux."
                )
            self._binary, player_args = pick
            self._args = tuple(player_args) + tuple(extra_args)
        self._process: Optional[subprocess.Popen[bytes]] = None

    @classmethod
    def probe(cls) -> bool:
        """Return True iff at least one supported player is installed."""
        return cls._probe(cls.DEFAULT_CANDIDATES) is not None

    @staticmethod
    def _probe(
        candidates: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> Optional[tuple[str, tuple[str, ...]]]:
        """Return the first (resolved-path, args) pair whose binary is on PATH."""
        for name, args in candidates:
            resolved = shutil.which(name)
            if resolved is not None:
                return resolved, args
        return None

    @property
    def binary(self) -> str:
        """Path of the player binary this sink uses (for diagnostics)."""
        return self._binary

    def play(self, buffer: AudioBuffer) -> None:
        """Render the buffer to WAV bytes and pipe them to the player."""
        self._kill_previous()
        data = buffer_to_wav_bytes(buffer)
        try:
            self._process = subprocess.Popen(
                [self._binary, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            # The probe passed at __init__ but the binary vanished —
            # surface as a recoverable unavailability so the caller
            # can choose to swap the sink rather than crash.
            raise LiveAudioUnavailable(
                f"{self._binary} disappeared between probe and play"
            ) from exc
        if self._process.stdin is None:
            return  # pragma: no cover - Popen with stdin=PIPE always sets it.
        try:
            self._process.stdin.write(data)
            self._process.stdin.close()
        except (BrokenPipeError, ValueError):
            # The player exited before we could finish writing — this
            # can happen when the next `play()` kills the prior one
            # mid-write, or when the device is suddenly unavailable.
            # Neither case is fatal: the subsequent play() will try
            # fresh.
            pass

    def close(self) -> None:
        """Stop any in-flight clip so the process exits quietly."""
        self._kill_previous()

    def _kill_previous(self) -> None:
        """Terminate the previous player process if one is still running."""
        prev = self._process
        if prev is None:
            return
        if prev.poll() is None:
            try:
                prev.terminate()
            except ProcessLookupError:
                pass
            try:
                prev.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                try:
                    prev.kill()
                except ProcessLookupError:
                    pass
                try:
                    prev.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
        self._process = None


def pick_live_sink() -> AudioSink:
    """Return the live sink that fits this host, or raise.

    Prefers the platform-native backend: ``WindowsLiveAudioSink`` on
    Windows (winsound), ``PosixLiveAudioSink`` on POSIX hosts that
    have a command-line player installed. Raises
    ``LiveAudioUnavailable`` only when none of those are usable so
    the CLI can drop down to ``MemorySink`` with a clear hint about
    which binary to install (F6).
    """
    if sys.platform.startswith("win"):
        return WindowsLiveAudioSink()
    try:
        return PosixLiveAudioSink()
    except LiveAudioUnavailable as exc:
        raise LiveAudioUnavailable(
            "no live audio sink is available on this host — "
            f"{exc} Alternative: capture audio with --wav-dir DIR.",
        ) from exc
