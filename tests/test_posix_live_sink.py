"""Unit tests for ``PosixLiveAudioSink`` (F6, PR 1).

POSIX live audio is routed through whatever command-line player
happens to be on PATH (paplay, aplay, afplay). These tests keep the
spawning end of that pipe honest without ever launching a real player:

* Probe logic: ``pick_live_sink`` / ``PosixLiveAudioSink()`` must pick
  the first available candidate in priority order, and raise
  ``LiveAudioUnavailable`` when none of them are present.
* ``play`` must render the buffer to WAV bytes, spawn the player with
  the right argv, and pipe the data in on stdin without blocking.
* Consecutive ``play()`` calls must kill the prior process so the most
  recent cue wins (matches the Windows sink's "latest event wins"
  semantics).
* ``close`` must terminate any still-running subprocess so tests and
  real shutdowns are clean.
"""

from __future__ import annotations

import io
import sys
import unittest
from unittest import mock

from asat.audio import AudioBuffer
from asat.audio_sink import (
    LiveAudioUnavailable,
    PosixLiveAudioSink,
    pick_live_sink,
)


def _buffer() -> AudioBuffer:
    """Return a tiny deterministic buffer for pipe-write assertions."""
    return AudioBuffer.mono([0.1, -0.1, 0.2, -0.2], sample_rate=8000)


class _RecordingStdin:
    """BytesIO wrapper that retains written bytes after ``close()``.

    The sink closes stdin right after writing, which collapses
    ``io.BytesIO.getvalue()``. Snapshotting into ``written`` on close
    keeps the payload visible to the test.
    """

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def close(self) -> None:
        self.written = self._buf.getvalue()
        self._buf.close()
        self.closed = True


class _FakeProcess:
    """Stand-in for ``subprocess.Popen`` that records what was written."""

    def __init__(self, argv: list[str], **_kwargs: object) -> None:
        self.argv = argv
        self.stdin = _RecordingStdin()
        self.terminated = False
        self.killed = False
        self.wait_called = 0
        self._poll_result: "int | None" = None

    def poll(self) -> "int | None":
        return self._poll_result

    def terminate(self) -> None:
        self.terminated = True
        # After terminate() the process is "still running" until wait()
        # observes the exit — leave poll() returning None so the sink
        # progresses into the wait branch.

    def kill(self) -> None:
        self.killed = True
        self._poll_result = -9

    def wait(self, timeout: float = 0.0) -> int:
        self.wait_called += 1
        self._poll_result = 0
        return 0


class ProbeTests(unittest.TestCase):

    def test_picks_first_available_candidate(self) -> None:
        def fake_which(name: str) -> "str | None":
            return "/usr/bin/paplay" if name == "paplay" else None

        with mock.patch("asat.audio_sink.shutil.which", side_effect=fake_which):
            sink = PosixLiveAudioSink()
        self.assertEqual(sink.binary, "/usr/bin/paplay")

    def test_falls_through_to_aplay_when_pulse_absent(self) -> None:
        def fake_which(name: str) -> "str | None":
            return "/usr/bin/aplay" if name == "aplay" else None

        with mock.patch("asat.audio_sink.shutil.which", side_effect=fake_which):
            sink = PosixLiveAudioSink()
        self.assertEqual(sink.binary, "/usr/bin/aplay")

    def test_raises_when_nothing_installed(self) -> None:
        with mock.patch("asat.audio_sink.shutil.which", return_value=None):
            with self.assertRaises(LiveAudioUnavailable):
                PosixLiveAudioSink()

    def test_probe_classmethod_reports_availability(self) -> None:
        with mock.patch("asat.audio_sink.shutil.which", return_value=None):
            self.assertFalse(PosixLiveAudioSink.probe())
        with mock.patch(
            "asat.audio_sink.shutil.which",
            lambda name: "/usr/bin/paplay" if name == "paplay" else None,
        ):
            self.assertTrue(PosixLiveAudioSink.probe())

    def test_explicit_binary_is_validated(self) -> None:
        with mock.patch("asat.audio_sink.shutil.which", return_value=None):
            with self.assertRaises(LiveAudioUnavailable):
                PosixLiveAudioSink(binary="aplay")


class PlayTests(unittest.TestCase):

    def setUp(self) -> None:
        self.which_patcher = mock.patch(
            "asat.audio_sink.shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "aplay" else None,
        )
        self.which_patcher.start()
        self.addCleanup(self.which_patcher.stop)
        self.processes: list[_FakeProcess] = []

        def fake_popen(argv: list[str], **kwargs: object) -> _FakeProcess:
            proc = _FakeProcess(argv, **kwargs)
            self.processes.append(proc)
            return proc

        self.popen_patcher = mock.patch(
            "asat.audio_sink.subprocess.Popen", side_effect=fake_popen
        )
        self.popen_patcher.start()
        self.addCleanup(self.popen_patcher.stop)

    def test_play_spawns_player_with_args(self) -> None:
        sink = PosixLiveAudioSink()
        sink.play(_buffer())
        self.assertEqual(len(self.processes), 1)
        proc = self.processes[0]
        # aplay is invoked with `-q` to keep stderr quiet.
        self.assertEqual(proc.argv[0], "/usr/bin/aplay")
        self.assertIn("-q", proc.argv)

    def test_play_writes_wav_bytes_to_stdin(self) -> None:
        sink = PosixLiveAudioSink()
        sink.play(_buffer())
        written = self.processes[0].stdin.written
        self.assertTrue(written.startswith(b"RIFF"))
        self.assertIn(b"WAVE", written)
        self.assertTrue(self.processes[0].stdin.closed)

    def test_consecutive_plays_kill_previous_process(self) -> None:
        sink = PosixLiveAudioSink()
        sink.play(_buffer())
        sink.play(_buffer())
        self.assertEqual(len(self.processes), 2)
        # The first process must have been terminated once the second
        # play started — "latest cue wins".
        self.assertTrue(self.processes[0].terminated)
        self.assertFalse(self.processes[1].terminated)

    def test_close_terminates_running_process(self) -> None:
        sink = PosixLiveAudioSink()
        sink.play(_buffer())
        sink.close()
        self.assertTrue(self.processes[0].terminated)

    def test_close_on_idle_sink_is_safe(self) -> None:
        sink = PosixLiveAudioSink()
        # No play() yet — close() must be a no-op that does not spawn
        # or touch any process.
        sink.close()
        self.assertEqual(self.processes, [])


class PickLiveSinkTests(unittest.TestCase):

    def test_raises_with_install_hint_on_posix_without_player(self) -> None:
        with mock.patch.object(sys, "platform", "linux"), mock.patch(
            "asat.audio_sink.shutil.which", return_value=None
        ):
            with self.assertRaises(LiveAudioUnavailable) as ctx:
                pick_live_sink()
        # The outer error must mention --wav-dir so the CLI user sees
        # a usable fallback.
        self.assertIn("--wav-dir", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
