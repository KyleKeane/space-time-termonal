"""SpatialAudioEngine: ties TTS, spatialization, and the sink to events.

The engine subscribes to events on the bus and converts them into
spatialized audio that lands in the provided sink. It owns:

- A TTSEngine for producing mono waveforms from text.
- A Spatializer for converting mono into binaural stereo.
- An AudioSink as the final destination.
- A VoiceRouter that picks a VoiceProfile and HRTFProfile for each
  incoming event.

Routing is deliberately simple in Phase 3: stdout chunks use the
stdout voice, stderr chunks use the stderr voice, command completion
and failure use the notification voice with short spoken cues. Later
phases extend the router with user-configurable maps and with
per-cell overrides.

The engine never blocks on audio hardware. Playback is synchronous
through whatever sink is supplied; if the sink writes to memory or a
file, the engine returns immediately. A live-speaker sink will be
introduced in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from asat.audio import AudioBuffer, VoiceProfile
from asat.audio_sink import AudioSink
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.hrtf import HRTFProfile, Spatializer
from asat.tts import TTSEngine


@dataclass
class VoiceRouter:
    """Maps event types to the voice profile used when speaking them.

    Fields are mutable so callers can replace individual entries at
    runtime without rebuilding the whole router.
    """

    stdout: VoiceProfile = field(default_factory=VoiceProfile.stdout_default)
    stderr: VoiceProfile = field(default_factory=VoiceProfile.stderr_default)
    notification: VoiceProfile = field(default_factory=VoiceProfile.notification_default)

    def voice_for(self, event_type: EventType) -> Optional[VoiceProfile]:
        """Return the voice profile to use for an event, or None to skip."""
        if event_type == EventType.OUTPUT_CHUNK:
            return self.stdout
        if event_type == EventType.ERROR_CHUNK:
            return self.stderr
        if event_type in (EventType.COMMAND_COMPLETED, EventType.COMMAND_FAILED):
            return self.notification
        return None


class SpatialAudioEngine:
    """Orchestrator that converts events into spatialized audio."""

    def __init__(
        self,
        bus: EventBus,
        tts: TTSEngine,
        spatializer: Spatializer,
        sink: AudioSink,
        router: Optional[VoiceRouter] = None,
    ) -> None:
        """Wire the engine into the bus and take ownership of its sink."""
        self._bus = bus
        self._tts = tts
        self._spatializer = spatializer
        self._sink = sink
        self._router = router or VoiceRouter()
        self._subscribe()

    def _subscribe(self) -> None:
        """Register the engine for the events it reacts to."""
        for event_type in (
            EventType.OUTPUT_CHUNK,
            EventType.ERROR_CHUNK,
            EventType.COMMAND_COMPLETED,
            EventType.COMMAND_FAILED,
        ):
            self._bus.subscribe(event_type, self._dispatch)

    def speak(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Render text through the pipeline and send it to the sink.

        Returns the stereo buffer that was sent to the sink so callers
        and tests can inspect the result directly.
        """
        mono = self._tts.synthesize(text, voice)
        profile = HRTFProfile.synthetic(voice.position, sample_rate=mono.sample_rate)
        stereo = self._spatializer.spatialize(mono, profile)
        self._sink.play(stereo)
        return stereo

    def close(self) -> None:
        """Unsubscribe from the bus and close the underlying sink."""
        for event_type in (
            EventType.OUTPUT_CHUNK,
            EventType.ERROR_CHUNK,
            EventType.COMMAND_COMPLETED,
            EventType.COMMAND_FAILED,
        ):
            self._bus.unsubscribe(event_type, self._dispatch)
        self._sink.close()

    def _dispatch(self, event: Event) -> None:
        """Translate a single event into at most one speak call."""
        voice = self._router.voice_for(event.event_type)
        if voice is None:
            return
        text = self._text_for_event(event)
        if not text:
            return
        self.speak(text, voice)

    @staticmethod
    def _text_for_event(event: Event) -> str:
        """Extract the text to speak for a given event's payload."""
        if event.event_type in (EventType.OUTPUT_CHUNK, EventType.ERROR_CHUNK):
            return (event.payload.get("line") or "").strip()
        if event.event_type == EventType.COMMAND_COMPLETED:
            return "completed"
        if event.event_type == EventType.COMMAND_FAILED:
            exit_code = event.payload.get("exit_code")
            if event.payload.get("timed_out"):
                return "timed out"
            if isinstance(exit_code, int):
                return f"failed with exit code {exit_code}"
            return "failed"
        return ""
