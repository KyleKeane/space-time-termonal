"""Unit tests for the audio sinks."""

from __future__ import annotations

import io
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from asat.audio import AudioBuffer
from asat.audio_sink import (
    LiveAudioUnavailable,
    MemorySink,
    WavFileSink,
    buffer_to_wav_bytes,
    pick_live_sink,
    write_wav,
)


class MemorySinkTests(unittest.TestCase):

    def test_records_every_buffer(self) -> None:
        sink = MemorySink()
        first = AudioBuffer.mono([0.1, 0.2])
        second = AudioBuffer.mono([0.3])
        sink.play(first)
        sink.play(second)
        self.assertEqual(sink.buffers, (first, second))

    def test_reset_clears_recording(self) -> None:
        sink = MemorySink()
        sink.play(AudioBuffer.mono([0.1]))
        sink.reset()
        self.assertEqual(sink.buffers, ())


class WavFileSinkTests(unittest.TestCase):

    def test_writes_one_file_per_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = WavFileSink(tmp)
            sink.play(AudioBuffer.mono([0.1, 0.2], sample_rate=8000))
            sink.play(AudioBuffer.stereo([0.1], [0.2], sample_rate=8000))
            written = sink.written_files
        self.assertEqual(len(written), 2)
        self.assertTrue(written[0].name.endswith("0001.wav"))
        self.assertTrue(written[1].name.endswith("0002.wav"))

    def test_written_wav_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = WavFileSink(tmp)
            sink.play(AudioBuffer.stereo([0.25, -0.25], [0.5, -0.5], sample_rate=8000))
            path = sink.written_files[0]
            with wave.open(str(path), "rb") as reader:
                self.assertEqual(reader.getnchannels(), 2)
                self.assertEqual(reader.getframerate(), 8000)
                self.assertEqual(reader.getsampwidth(), 2)
                self.assertEqual(reader.getnframes(), 2)


class WriteWavTests(unittest.TestCase):

    def test_mono_values_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mono.wav"
            buffer = AudioBuffer.mono([0.0, 0.5, -0.5, 1.0, -1.0], sample_rate=8000)
            write_wav(path, buffer)
            with wave.open(str(path), "rb") as reader:
                raw = reader.readframes(reader.getnframes())
                channels = reader.getnchannels()
                sample_rate = reader.getframerate()
        self.assertEqual(channels, 1)
        self.assertEqual(sample_rate, 8000)
        values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
        self.assertEqual(values[0], 0)
        self.assertGreater(values[1], 16000)
        self.assertLess(values[2], -16000)
        self.assertEqual(values[3], 32767)
        self.assertEqual(values[4], -32767)

    def test_values_are_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hot.wav"
            buffer = AudioBuffer.mono([5.0, -5.0], sample_rate=8000)
            write_wav(path, buffer)
            with wave.open(str(path), "rb") as reader:
                raw = reader.readframes(reader.getnframes())
        values = struct.unpack("<hh", raw)
        self.assertEqual(values[0], 32767)
        self.assertEqual(values[1], -32767)


class BufferToWavBytesTests(unittest.TestCase):

    def test_produces_a_wav_blob_the_wave_module_can_reread(self) -> None:
        buffer = AudioBuffer.stereo([0.25, -0.25], [0.5, -0.5], sample_rate=8000)
        blob = buffer_to_wav_bytes(buffer)
        self.assertTrue(blob.startswith(b"RIFF"))
        self.assertIn(b"WAVE", blob[:12])
        with wave.open(io.BytesIO(blob), "rb") as reader:
            self.assertEqual(reader.getnchannels(), 2)
            self.assertEqual(reader.getframerate(), 8000)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertEqual(reader.getnframes(), 2)


class PickLiveSinkTests(unittest.TestCase):

    def test_non_windows_raises_live_audio_unavailable(self) -> None:
        # pick_live_sink branches on sys.platform; if we're not on
        # Windows it must fail cleanly so the CLI can fall back.
        if sys.platform.startswith("win"):
            self.skipTest("running on Windows; live sink is expected to succeed")
        with self.assertRaises(LiveAudioUnavailable):
            pick_live_sink()

    def test_windows_branch_uses_winsound_with_memory_async_flags(self) -> None:
        # Simulate the Windows branch by injecting a fake `winsound`
        # module and flipping sys.platform for the duration of the test.
        fake = mock.MagicMock()
        fake.SND_MEMORY = 0x0004
        fake.SND_ASYNC = 0x0001
        fake.SND_PURGE = 0x0040
        with mock.patch.dict(sys.modules, {"winsound": fake}):
            with mock.patch.object(sys, "platform", "win32"):
                sink = pick_live_sink()
                sink.play(AudioBuffer.mono([0.1, 0.2], sample_rate=8000))
                sink.close()
        call = fake.PlaySound.call_args_list[0]
        data, flags = call.args
        self.assertIsInstance(data, (bytes, bytearray))
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertEqual(flags, fake.SND_MEMORY | fake.SND_ASYNC)
        # close() asks PlaySound to flush any in-flight clip.
        fake.PlaySound.assert_called_with(None, fake.SND_PURGE)


if __name__ == "__main__":
    unittest.main()
