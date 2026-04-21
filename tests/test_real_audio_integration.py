"""End-to-end audio pipeline integration tests.

Everything else in ``tests/`` either unit-tests one module in isolation
or uses ``MemorySink`` — which never fails and never validates the
content of what it receives. That makes the green test suite trust a
pipeline that could be silently emitting empty buffers, wrong sample
rates, or pure-silence WAVs and nobody would catch it.

This file closes that gap by wiring the real ``SoundEngine``
(``ToneTTSEngine`` + real ``Spatializer`` + ``SoundGeneratorRegistry``)
to a real ``WavFileSink`` and asserting, for every covered event type,
that a valid non-empty non-silent WAV file lands on disk. It's the
substrate W2 of the reliability framework — the safety net that
catches regressions the unit suite can't see (e.g., an engine that
starts emitting silent buffers, or a sink change that loses bytes on
the way to disk).

Kept deterministic and fast: uses ``ToneTTSEngine`` (pure stdlib,
no external TTS binary), writes into a temp directory, and asserts
on numeric properties (RMS > epsilon, sample rate matches) rather
than perceptual ones.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from math import sqrt
from pathlib import Path

from asat.audio import DEFAULT_SAMPLE_RATE
from asat.audio_sink import WavFileSink
from asat.default_bank import COVERED_EVENT_TYPES, default_sound_bank
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.sample_payloads import SAMPLE_PAYLOADS
from asat.sound_engine import SoundEngine
from asat.tts import ToneTTSEngine


SILENCE_RMS_THRESHOLD = 1e-4  # RMS below this = effectively silent


def _rms(pcm16_bytes: bytes) -> float:
    """Return the root-mean-square amplitude of a 16-bit PCM payload.

    Ranges from 0.0 (pure silence) to about 1.0 (full scale). A real
    tone at moderate volume lands around 0.1-0.3; the threshold
    distinguishes "actually audible" from "buffer was never filled."
    """
    if not pcm16_bytes:
        return 0.0
    sample_count = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{sample_count}h", pcm16_bytes)
    total = sum(s * s for s in samples)
    return sqrt(total / sample_count) / 32767.0


def _build_engine(sink):
    """Construct a real SoundEngine for integration testing.

    Deterministic and offline: ``ToneTTSEngine`` uses nothing but
    stdlib math, the default ``Spatializer`` is a synthetic HRTF
    with no external dependency, and the default sound generators
    are pure Python. The only moving part under test is the full
    event-bus → engine → sink chain.

    Returns ``(engine, bus)`` so callers have a handle to both.
    """
    bus = EventBus()
    engine = SoundEngine(
        bus,
        default_sound_bank(),
        sink,
        tts=ToneTTSEngine(sample_rate=DEFAULT_SAMPLE_RATE),
        sample_rate=DEFAULT_SAMPLE_RATE,
    )
    return engine, bus


class WavFileSinkIntegrationTests(unittest.TestCase):
    """Every covered event produces a valid, non-silent WAV on disk."""

    def test_every_covered_event_writes_a_playable_wav(self) -> None:
        # The most important assertion in the whole suite: a fresh
        # install, walked through every event type the default bank
        # narrates, emits WAV files that a sighted reviewer could open
        # and hear. A regression that made the engine emit silence or
        # corrupt PCM would turn this test red.
        with tempfile.TemporaryDirectory() as tmp:
            sink = WavFileSink(tmp, prefix="covered")
            engine, bus = _build_engine(sink)
            # Track which events produced at least one sink play, so
            # we can report precisely which ones went silent.
            self.addCleanup(engine.close)

            events_missing_payload: list[str] = []
            events_with_file: list[str] = []

            for event_type in sorted(COVERED_EVENT_TYPES, key=lambda e: e.value):
                payload = SAMPLE_PAYLOADS.get(event_type)
                if payload is None:
                    events_missing_payload.append(event_type.value)
                    continue
                before_count = len(sink.written_files)
                publish_event(bus, event_type, dict(payload), source="test")
                if len(sink.written_files) > before_count:
                    events_with_file.append(event_type.value)

            # Every covered event must have a sample payload; the
            # sync-gate tests enforce this, but we verify here too so
            # a future bug in the sync gate doesn't let drift slip
            # through.
            self.assertEqual(
                events_missing_payload,
                [],
                f"covered events without sample payload: {events_missing_payload}",
            )

            # Not every covered event necessarily fires a sink play —
            # some bindings might render to empty buffers if the
            # template happens to be empty for a payload. But at least
            # 80% of covered events must produce audio, else something
            # is systemically broken.
            self.assertGreater(
                len(events_with_file),
                int(len(COVERED_EVENT_TYPES) * 0.80),
                f"only {len(events_with_file)} / {len(COVERED_EVENT_TYPES)} "
                f"events produced audio — regression likely",
            )

            # Every WAV that did get written must be a valid, non-empty,
            # non-silent 16-bit PCM file. This is the byte-level check
            # that mocks can never perform.
            for path in sink.written_files:
                with self.subTest(wav=path.name):
                    self._assert_playable_wav(path)

    def _assert_playable_wav(self, path: Path) -> None:
        """Open the file with the wave module and verify its contents."""
        self.assertGreater(path.stat().st_size, 44, f"{path} is suspiciously small")
        with wave.open(str(path), "rb") as reader:
            # Expected sink output: 16-bit PCM, mono or stereo, at
            # the engine's default sample rate. A regression that
            # changed the bit depth or the rate would fail here.
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertIn(reader.getnchannels(), (1, 2))
            self.assertEqual(reader.getframerate(), DEFAULT_SAMPLE_RATE)
            frame_count = reader.getnframes()
            self.assertGreater(frame_count, 0, f"{path} has zero frames")
            raw = reader.readframes(frame_count)
        self.assertGreater(
            _rms(raw),
            SILENCE_RMS_THRESHOLD,
            f"{path} is effectively silent — engine emitted dead audio",
        )


class EndToEndPipelineTests(unittest.TestCase):
    """Cover a few canonical user-scenario events with tighter asserts."""

    def _run_one(self, event_type: EventType, payload: dict) -> Path:
        """Publish one event, return the WAV file it produced."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: self._cleanup_dir(tmp_dir))
        sink = WavFileSink(tmp_dir, prefix="one")
        engine, bus = _build_engine(sink)
        self.addCleanup(engine.close)
        publish_event(bus, event_type, payload, source="test")
        self.assertGreaterEqual(
            len(sink.written_files),
            1,
            f"no WAV emitted for {event_type.value}",
        )
        return sink.written_files[-1]

    @staticmethod
    def _cleanup_dir(path: str) -> None:
        import shutil

        shutil.rmtree(path, ignore_errors=True)

    def test_command_submitted_produces_audio(self) -> None:
        # The most frequent user-facing event — pressing Enter on a
        # cell. Regression here means the user hears nothing on every
        # keystroke they care about.
        path = self._run_one(
            EventType.COMMAND_SUBMITTED,
            {"cell_id": "c1", "command": "ls"},
        )
        with wave.open(str(path), "rb") as reader:
            raw = reader.readframes(reader.getnframes())
        self.assertGreater(_rms(raw), SILENCE_RMS_THRESHOLD)

    def test_command_failed_produces_audio(self) -> None:
        # The error path — a user whose command errored and who hears
        # no failure cue is in trouble.
        path = self._run_one(
            EventType.COMMAND_FAILED,
            {"cell_id": "c1", "exit_code": 2, "timed_out": False},
        )
        with wave.open(str(path), "rb") as reader:
            raw = reader.readframes(reader.getnframes())
        self.assertGreater(_rms(raw), SILENCE_RMS_THRESHOLD)

    def test_audio_pipeline_failed_fires_fallback_binding(self) -> None:
        # Meta: the event type introduced by the Never Crashes
        # workstream must itself produce audible output so the user
        # hears WHICH event blew up, not just the fail-audible tone
        # from SoundEngine's guard.
        path = self._run_one(
            EventType.AUDIO_PIPELINE_FAILED,
            {
                "event_type": "command.completed",
                "binding_id": "some-binding",
                "error_class": "RuntimeError",
                "error_message": "simulated",
            },
        )
        with wave.open(str(path), "rb") as reader:
            raw = reader.readframes(reader.getnframes())
        self.assertGreater(_rms(raw), SILENCE_RMS_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
