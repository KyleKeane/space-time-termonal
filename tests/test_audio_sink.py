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

from asat.audio import AudioBuffer, ChannelLayout
from asat.audio_sink import (
    LiveAudioUnavailable,
    MemorySink,
    SoundDeviceSink,
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

    def test_non_windows_raises_when_no_backend_available(self) -> None:
        # With sounddevice patched unavailable and no POSIX player on
        # PATH, pick_live_sink must raise so the CLI can fall back.
        if sys.platform.startswith("win"):
            self.skipTest("running on Windows; live sink is expected to succeed")
        with mock.patch(
            "asat.audio_sink.SoundDeviceSink.__init__",
            side_effect=LiveAudioUnavailable("patched: sounddevice unavailable"),
        ):
            with mock.patch("asat.audio_sink.shutil.which", return_value=None):
                with self.assertRaises(LiveAudioUnavailable):
                    pick_live_sink()

    def test_prefers_sounddevice_when_available(self) -> None:
        # When SoundDeviceSink constructs successfully, pick_live_sink
        # must return it without ever touching the POSIX / Windows
        # fallbacks. This is the "just works after pip install" path.
        fake_sink = mock.MagicMock()
        with mock.patch(
            "asat.audio_sink.SoundDeviceSink",
            return_value=fake_sink,
        ) as ctor:
            got = pick_live_sink()
        self.assertIs(got, fake_sink)
        ctor.assert_called_once()

    def test_falls_back_to_winsound_on_windows_without_sounddevice(self) -> None:
        # When sounddevice fails and we're on Windows, the winsound
        # sink is the next layer of defense.
        fake = mock.MagicMock()
        fake.SND_MEMORY = 0x0004
        fake.SND_ASYNC = 0x0001
        fake.SND_PURGE = 0x0040
        with mock.patch(
            "asat.audio_sink.SoundDeviceSink.__init__",
            side_effect=LiveAudioUnavailable("patched: sounddevice unavailable"),
        ):
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
        fake.PlaySound.assert_called_with(None, fake.SND_PURGE)


class SoundDeviceSinkTests(unittest.TestCase):
    """Verify SoundDeviceSink orchestrates sounddevice + numpy correctly.

    The tests mock both libraries so they pass in CI environments
    that don't have sounddevice installed or an audio output device.
    """

    def _fake_modules(self):
        """Return (fake_sounddevice, fake_numpy) with minimal stubs."""
        fake_sd = mock.MagicMock()
        fake_np = mock.MagicMock()
        fake_np.float32 = "float32"
        # asarray returns the same mock array each call so tests can
        # assert on reshape / samplerate handling without tracking
        # call-specific return values.
        return fake_sd, fake_np

    def test_raises_live_audio_unavailable_when_sounddevice_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            with self.assertRaises(LiveAudioUnavailable):
                SoundDeviceSink()

    def test_raises_live_audio_unavailable_when_no_device(self) -> None:
        fake_sd, fake_np = self._fake_modules()
        fake_sd.check_output_settings = mock.MagicMock(
            side_effect=RuntimeError("no default output device")
        )
        with mock.patch.dict(
            sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}
        ):
            with self.assertRaises(LiveAudioUnavailable):
                SoundDeviceSink()

    def test_probe_returns_false_when_sounddevice_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            self.assertFalse(SoundDeviceSink.probe())

    def test_probe_returns_true_when_everything_works(self) -> None:
        fake_sd, fake_np = self._fake_modules()
        with mock.patch.dict(
            sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}
        ):
            self.assertTrue(SoundDeviceSink.probe())
        fake_sd.check_output_settings.assert_called_once()

    def test_play_mono_hands_buffer_to_sounddevice(self) -> None:
        fake_sd, fake_np = self._fake_modules()
        with mock.patch.dict(
            sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}
        ):
            sink = SoundDeviceSink()
            sink.play(AudioBuffer.mono([0.1, 0.2], sample_rate=8000))
        # Stop any prior clip, then play the fresh one — "latest wins".
        fake_sd.stop.assert_called()
        fake_sd.play.assert_called_once()
        kwargs = fake_sd.play.call_args.kwargs
        self.assertEqual(kwargs["samplerate"], 8000)
        self.assertFalse(kwargs["blocking"])

    def test_play_stereo_reshapes_to_n_by_2(self) -> None:
        fake_sd, fake_np = self._fake_modules()
        with mock.patch.dict(
            sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}
        ):
            sink = SoundDeviceSink()
            sink.play(
                AudioBuffer.stereo([0.1, 0.3], [0.2, 0.4], sample_rate=8000)
            )
        # The array returned by asarray should have been reshape(-1, 2)'d.
        asarray_result = fake_np.asarray.return_value
        asarray_result.reshape.assert_called_once_with(-1, 2)

    def test_close_stops_any_playing_clip(self) -> None:
        fake_sd, fake_np = self._fake_modules()
        with mock.patch.dict(
            sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}
        ):
            sink = SoundDeviceSink()
            fake_sd.stop.reset_mock()
            sink.close()
        fake_sd.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
