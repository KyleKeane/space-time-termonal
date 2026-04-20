"""Diagnostic ``--check`` self-test (F42).

A blind user setting up ASAT on a fresh machine needs a single
command that answers "does this install actually work?". This module
provides ``run_self_check``, the four-step routine that
``python -m asat --check`` drives:

1. **Bank validates.** ``bank.validate()`` catches structural issues
   (duplicate ids, dangling voice/sound references) before anything
   else runs.
2. **Every voice speaks.** Each voice in the bank renders a short
   canned phrase through the real TTS engine and routes it to the
   active sink. Catches missing TTS backends, broken voice profiles,
   or a sink that swallows speech.
3. **One cue per covered event.** Every ``COVERED_EVENT_TYPES`` member
   is published with its representative payload from
   ``SAMPLE_PAYLOADS``; we confirm at least one buffer lands on the
   sink for each. Catches predicate typos, missing bindings, and
   payload-shape drift between docs and the engine.
4. **Live playback reachable.** Reports the resolved sink type so the
   user knows whether they are about to hear audio or watch silent
   buffers accumulate in memory. Informational when ``--live`` was
   not requested, FAIL when it was requested but the host fell back
   to a silent sink.

Each step prints a one-line ``PASS / FAIL / SKIP`` to ``stdout`` and
publishes a ``SELF_CHECK_STEP`` event on the bus (when a bus is
provided) so the F22 diagnostic log and any future viewer can record
the run. The exit code is ``0`` when every step passed, ``1``
otherwise — that is what ``--check`` returns.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional, TextIO

from asat.audio_sink import AudioSink, MemorySink
from asat.default_bank import COVERED_EVENT_TYPES
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.sample_payloads import SAMPLE_PAYLOADS
from asat.sound_bank import SoundBank
from asat.sound_engine import SoundEngine
from asat.tts_registry import TTSEngineRegistry


@dataclass(frozen=True)
class SelfCheckStep:
    """Outcome of one self-check step.

    ``slug`` is the stable identifier (used in the SELF_CHECK_STEP
    payload and in tests). ``status`` is ``"pass"``, ``"fail"``, or
    ``"skip"``. ``detail`` is a short human-readable summary so the
    JSONL log is skimmable without cross-referencing source code.
    """

    slug: str
    status: str
    detail: str


_SLUGS = (
    "bank_validates",
    "tts_engine",
    "voices_speak",
    "event_cues",
    "live_playback",
)


def run_self_check(
    bank: SoundBank,
    sink: AudioSink,
    *,
    bus: Optional[EventBus] = None,
    stdout: TextIO = sys.stdout,
    live_requested: bool = False,
) -> int:
    """Run the four-step self-test, return ``0`` when all steps pass.

    ``bank`` and ``sink`` are the resolved bank and sink the live
    Application would use, so the diagnostic exercises the same code
    path real audio takes. ``bus``, if provided, receives one
    ``SELF_CHECK_STEP`` event per step (so a JSONL logger or future
    diagnostic-log viewer can capture the run); ``None`` is fine for
    an unattached invocation. ``live_requested`` reflects ``--live``
    so step 4 can fail when the user asked for live audio but the
    host fell back to ``MemorySink``.
    """
    steps: list[SelfCheckStep] = []
    steps.append(_step_bank_validates(bank))
    bank_ok = steps[-1].status == "pass"
    steps.append(_step_tts_engine())
    # Only build the engine when the bank is structurally sound;
    # ``SoundEngine.__init__`` calls ``bank.validate()`` and would
    # raise on a broken bank, taking the diagnostic down with it.
    inner_bus = EventBus()
    probe = _ProbingSink(sink)
    engine: Optional[SoundEngine] = None
    if bank_ok:
        engine = SoundEngine(inner_bus, bank, probe)
    try:
        steps.append(_step_voices_speak(bank, engine, bank_ok))
        steps.append(_step_event_cues(bank, inner_bus, probe, bank_ok))
        steps.append(_step_live_playback(sink, live_requested))
    finally:
        if engine is not None:
            engine.close()
    total = len(steps)
    for index, step in enumerate(steps, start=1):
        stdout.write(f"[{index}/{total}] {step.status.upper():4s} {step.slug} — {step.detail}\n")
        if bus is not None:
            publish_event(
                bus,
                EventType.SELF_CHECK_STEP,
                {
                    "step": step.slug,
                    "status": step.status,
                    "index": index,
                    "total": total,
                    "detail": step.detail,
                },
                source="self_check",
            )
    return 0 if all(step.status == "pass" for step in steps) else 1


def _step_bank_validates(bank: SoundBank) -> SelfCheckStep:
    """Step 1: structural validation of the bank."""
    try:
        bank.validate()
    except Exception as exc:  # SoundBankError or any nested issue.
        return SelfCheckStep(
            slug="bank_validates",
            status="fail",
            detail=f"bank.validate() raised: {exc}",
        )
    return SelfCheckStep(
        slug="bank_validates",
        status="pass",
        detail=(
            f"{len(bank.voices)} voices, {len(bank.sounds)} sounds, "
            f"{len(bank.bindings)} bindings"
        ),
    )


def _step_tts_engine() -> SelfCheckStep:
    """Step 2: report which TTS engine the default registry resolves to.

    The resolved engine is the one ``__main__`` will instantiate when
    the user runs ``python -m asat`` with no ``--tts`` flag. Reporting
    it here tells the operator at a glance whether they are about to
    hear pyttsx3, espeak-ng, macOS ``say``, or the deterministic tone
    fallback — a frequent source of "why is my audio beeping?"
    confusion. ``tone`` is always available, so this step cannot
    ``fail`` in practice; it is effectively an informational probe.
    """
    registry = TTSEngineRegistry.default()
    available = registry.available_ids()
    resolved = registry.resolve_default_id()
    if not available:
        return SelfCheckStep(
            slug="tts_engine",
            status="fail",
            detail="no TTS engines available (expected at least 'tone')",
        )
    alternatives = ", ".join(available)
    return SelfCheckStep(
        slug="tts_engine",
        status="pass",
        detail=f"resolved engine: {resolved}; available: {alternatives}",
    )


def _step_voices_speak(
    bank: SoundBank, engine: Optional[SoundEngine], bank_ok: bool
) -> SelfCheckStep:
    """Step 2: every voice synthesises through the live TTS path."""
    if not bank_ok or engine is None:
        return SelfCheckStep(
            slug="voices_speak",
            status="skip",
            detail="skipped: bank failed to validate",
        )
    if not bank.voices:
        return SelfCheckStep(
            slug="voices_speak",
            status="fail",
            detail="bank defines no voices",
        )
    failed: list[str] = []
    for voice in bank.voices:
        buffer = engine.speak(voice.id, f"voice {voice.id} check")
        if buffer is None:
            failed.append(voice.id)
    if failed:
        return SelfCheckStep(
            slug="voices_speak",
            status="fail",
            detail=f"voices that did not render: {', '.join(failed)}",
        )
    voice_ids = ", ".join(voice.id for voice in bank.voices)
    return SelfCheckStep(
        slug="voices_speak",
        status="pass",
        detail=f"all {len(bank.voices)} voices rendered ({voice_ids})",
    )


def _step_event_cues(
    bank: SoundBank,
    inner_bus: EventBus,
    probe: "_ProbingSink",
    bank_ok: bool,
) -> SelfCheckStep:
    """Step 3: every covered event yields at least one buffer on the sink."""
    if not bank_ok:
        return SelfCheckStep(
            slug="event_cues",
            status="skip",
            detail="skipped: bank failed to validate",
        )
    silent: list[str] = []
    for event_type in COVERED_EVENT_TYPES:
        payload = SAMPLE_PAYLOADS.get(event_type)
        if payload is None:
            silent.append(f"{event_type.value} (no sample payload)")
            continue
        before = probe.buffer_count
        publish_event(inner_bus, event_type, dict(payload), source="self_check")
        if probe.buffer_count == before:
            silent.append(event_type.value)
    if silent:
        return SelfCheckStep(
            slug="event_cues",
            status="fail",
            detail=f"events with no buffer on the sink: {', '.join(silent)}",
        )
    return SelfCheckStep(
        slug="event_cues",
        status="pass",
        detail=f"all {len(COVERED_EVENT_TYPES)} covered events produced audio",
    )


def _step_live_playback(sink: AudioSink, live_requested: bool) -> SelfCheckStep:
    """Step 4: report the resolved sink so the user knows where audio went."""
    sink_name = type(sink).__name__
    is_memory = isinstance(sink, MemorySink)
    if live_requested and is_memory:
        return SelfCheckStep(
            slug="live_playback",
            status="fail",
            detail=(
                "--live requested but the platform fell back to MemorySink; "
                "audio is not reaching the speakers"
            ),
        )
    if is_memory:
        return SelfCheckStep(
            slug="live_playback",
            status="pass",
            detail=(
                "MemorySink active (audio is buffered, not played). "
                "Pass --live or --wav-dir DIR to hear or capture it."
            ),
        )
    return SelfCheckStep(
        slug="live_playback",
        status="pass",
        detail=f"sink is {sink_name}; live playback reachable",
    )


class _ProbingSink:
    """Wrap a sink to count how many buffers have been played.

    The self-check needs to know "did event X cause at least one
    buffer to land on the sink?" without assuming the sink type.
    Counting is the simplest test that works for ``MemorySink``,
    ``WavFileSink``, the live backend, and any tee composition. The
    inner sink is *not* closed when this wrapper closes — ownership
    stays with the caller.
    """

    def __init__(self, inner: AudioSink) -> None:
        self._inner = inner
        self.buffer_count = 0

    def play(self, buffer) -> None:
        """Forward to the inner sink and bump the counter."""
        self._inner.play(buffer)
        self.buffer_count += 1

    def close(self) -> None:
        """No-op; the inner sink belongs to the caller."""
        return None
