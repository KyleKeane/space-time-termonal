"""Tests for the F42 ``--check`` self-test routine."""

from __future__ import annotations

import io
import unittest

from asat.audio import AudioBuffer
from asat.audio_sink import MemorySink
from asat.default_bank import default_sound_bank
from asat.event_bus import EventBus
from asat.events import EventType
from asat.self_check import SelfCheckStep, run_self_check
from asat.sound_bank import EventBinding, SoundBank


class _CountingSink:
    """AudioSink that records every buffer it receives.

    Mirrors MemorySink for counting purposes but is its own class so
    ``isinstance(_, MemorySink)`` is False — that lets us cover the
    "sink is a real backend" branch of step 4 without depending on a
    platform live sink that might not exist on the test host.
    """

    def __init__(self) -> None:
        self.buffers: list[AudioBuffer] = []

    def play(self, buffer: AudioBuffer) -> None:
        self.buffers.append(buffer)

    def close(self) -> None:
        return None


class RunSelfCheckHappyPathTests(unittest.TestCase):

    def test_default_bank_with_memory_sink_passes_every_step(self) -> None:
        sink = MemorySink()
        bus = EventBus()
        captured: list[dict] = []
        bus.subscribe(
            EventType.SELF_CHECK_STEP, lambda event: captured.append(event.payload)
        )
        stdout = io.StringIO()

        exit_code = run_self_check(
            default_sound_bank(),
            sink,
            bus=bus,
            stdout=stdout,
            live_requested=False,
        )

        self.assertEqual(exit_code, 0)
        slugs = [payload["step"] for payload in captured]
        self.assertEqual(
            slugs,
            [
                "bank_validates",
                "tts_engine",
                "voices_speak",
                "event_cues",
                "live_playback",
            ],
        )
        for payload in captured:
            self.assertEqual(payload["status"], "pass", payload)
            self.assertIn("detail", payload)
            self.assertEqual(payload["total"], 5)
        self.assertEqual(
            [payload["index"] for payload in captured], [1, 2, 3, 4, 5]
        )
        text = stdout.getvalue()
        self.assertIn("PASS bank_validates", text)
        self.assertIn("PASS tts_engine", text)
        self.assertIn("PASS voices_speak", text)
        self.assertIn("PASS event_cues", text)
        self.assertIn("PASS live_playback", text)

    def test_passes_with_real_sink_class_and_no_bus(self) -> None:
        """The bus argument is optional; non-MemorySink sinks pass step 4."""
        sink = _CountingSink()
        stdout = io.StringIO()

        exit_code = run_self_check(
            default_sound_bank(),
            sink,
            stdout=stdout,
            live_requested=True,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("_CountingSink", stdout.getvalue())
        self.assertIn("live playback reachable", stdout.getvalue())
        # The real sink should have received the step-2 voice utterances
        # plus the step-3 event cues (one buffer per covered event +
        # three voice phrases at minimum).
        self.assertGreater(len(sink.buffers), 30)


class RunSelfCheckFailureTests(unittest.TestCase):

    def test_live_requested_but_memory_sink_fails_step_four(self) -> None:
        sink = MemorySink()
        stdout = io.StringIO()

        exit_code = run_self_check(
            default_sound_bank(),
            sink,
            stdout=stdout,
            live_requested=True,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("FAIL live_playback", stdout.getvalue())
        self.assertIn("--live requested", stdout.getvalue())

    def test_bank_with_no_voices_fails_voices_speak(self) -> None:
        # An empty bank still validates, but step 2 has nothing to render.
        empty_bank = SoundBank()
        sink = MemorySink()
        stdout = io.StringIO()

        exit_code = run_self_check(empty_bank, sink, stdout=stdout)

        self.assertEqual(exit_code, 1)
        text = stdout.getvalue()
        self.assertIn("PASS bank_validates", text)
        self.assertIn("FAIL voices_speak", text)
        # An empty bank also has zero bindings, so the "no buffer" check
        # for every covered event still fails — it's the bank's fault,
        # not the engine's, and the failure is reported per-event.
        self.assertIn("FAIL event_cues", text)

    def test_invalid_bank_marks_dependent_steps_skip(self) -> None:
        # Build a bank whose binding points at a missing voice id.
        # validate() catches this and marks step 1 fail; steps 2/3 then
        # skip because they depend on a usable bank.
        bad_bank = SoundBank(
            bindings=(
                EventBinding(
                    id="b1",
                    event_type=EventType.OUTPUT_CHUNK.value,
                    voice_id="ghost",
                    say_template="hi",
                ),
            ),
        )
        sink = MemorySink()
        stdout = io.StringIO()

        exit_code = run_self_check(bad_bank, sink, stdout=stdout)

        self.assertEqual(exit_code, 1)
        text = stdout.getvalue()
        self.assertIn("FAIL bank_validates", text)
        self.assertIn("SKIP voices_speak", text)
        self.assertIn("SKIP event_cues", text)
        # Step 4 is independent of the bank and still passes.
        self.assertIn("PASS live_playback", text)


class SelfCheckEventEmissionTests(unittest.TestCase):

    def test_every_step_publishes_self_check_step_event(self) -> None:
        sink = MemorySink()
        bus = EventBus()
        captured: list[dict] = []
        bus.subscribe(
            EventType.SELF_CHECK_STEP, lambda event: captured.append(event.payload)
        )

        run_self_check(default_sound_bank(), sink, bus=bus, stdout=io.StringIO())

        self.assertEqual(len(captured), 5)
        for index, payload in enumerate(captured, start=1):
            self.assertEqual(payload["index"], index)
            self.assertEqual(payload["total"], 5)
            self.assertIn(payload["status"], {"pass", "fail", "skip"})
            self.assertIsInstance(payload["step"], str)
            self.assertIsInstance(payload["detail"], str)


class SelfCheckStepDataclassTests(unittest.TestCase):

    def test_step_carries_slug_status_detail(self) -> None:
        step = SelfCheckStep(slug="bank_validates", status="pass", detail="ok")
        self.assertEqual(step.slug, "bank_validates")
        self.assertEqual(step.status, "pass")
        self.assertEqual(step.detail, "ok")


if __name__ == "__main__":
    unittest.main()
