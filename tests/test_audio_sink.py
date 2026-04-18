"""Unit tests for the audio sinks."""

from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

from asat.audio import AudioBuffer
from asat.audio_sink import MemorySink, WavFileSink, write_wav


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


if __name__ == "__main__":
    unittest.main()
