"""Text-to-speech abstraction.

TTSEngine is a Protocol that any speech backend can satisfy. This keeps
the rest of the audio pipeline independent of which engine synthesizes
the words. ASAT ships with one reference implementation:

- ToneTTSEngine produces a deterministic tonal waveform. It is not
  intelligible speech; it is a pipeline placeholder that proves the
  flow end to end without depending on a real speech synthesizer.
  Tests use it because it generates the same output for the same
  input every time, and a real TTS engine would need audio hardware
  and a system speech library.

Future backends (espeak-ng via subprocess, pyttsx3, SAPI, AVFoundation)
drop in under the same protocol. Per the project's security posture,
any new backend should be a thin, auditable wrapper around a
well-known local engine, never a network call.
"""

from __future__ import annotations

import math
from typing import Protocol

from asat.audio import AudioBuffer, DEFAULT_SAMPLE_RATE, VoiceProfile


class TTSEngine(Protocol):
    """Synthesizes a line of text into a mono AudioBuffer.

    Single in-tree implementation today: ``ToneTTSEngine`` (a
    deterministic tone-based stand-in). Pluggable: real TTS
    backends (Windows SAPI, macOS NSSpeechSynthesizer, Piper, etc.)
    can be swapped in via ``SoundEngine(..., tts=...)`` without
    touching the rest of the pipeline.
    """

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Return a mono AudioBuffer containing the rendered speech."""
        ...


class ToneTTSEngine:
    """Deterministic tone-based stand-in for a real speech synthesizer.

    For each character of input text, the engine emits a short sine
    tone whose frequency is derived from the character's code point
    and the voice's base pitch. The duration of each tone is
    controlled by the voice's words-per-minute setting. The volume
    field of the voice is applied as a linear gain.

    The result is a reproducible waveform suitable for pipeline
    testing and manual sanity checks. It is not speech.
    """

    CHARACTERS_PER_WORD = 5
    SEMITONE_STEP = 0.06

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        """Store the sample rate all produced buffers will use."""
        self._sample_rate = sample_rate

    @property
    def sample_rate(self) -> int:
        """Return the sample rate of every buffer this engine produces."""
        return self._sample_rate

    def synthesize(self, text: str, voice: VoiceProfile) -> AudioBuffer:
        """Render the given text into a mono tone buffer.

        Empty or whitespace-only text returns a very short silence so
        downstream stages always receive a well-formed buffer.
        """
        if not text or text.isspace():
            return AudioBuffer.silence(0.05, self._sample_rate)
        samples_per_char = self._samples_per_character(voice)
        waveform: list[float] = []
        for index, character in enumerate(text):
            frequency = self._frequency_for_character(character, voice.pitch_hz)
            self._append_tone(waveform, frequency, samples_per_char, voice.volume)
        return AudioBuffer.mono(waveform, self._sample_rate)

    def _samples_per_character(self, voice: VoiceProfile) -> int:
        """Translate words-per-minute into samples per character."""
        effective_wpm = max(voice.speed_wpm, 30.0)
        chars_per_second = (effective_wpm * self.CHARACTERS_PER_WORD) / 60.0
        duration_seconds = 1.0 / max(chars_per_second, 1e-3)
        return max(1, int(duration_seconds * self._sample_rate))

    def _frequency_for_character(self, character: str, base_pitch: float) -> float:
        """Map a character to a frequency offset from the base pitch."""
        offset_semitones = ord(character) % 12
        return base_pitch * (1.0 + offset_semitones * self.SEMITONE_STEP)

    def _append_tone(
        self,
        waveform: list[float],
        frequency: float,
        sample_count: int,
        volume: float,
    ) -> None:
        """Append a sine tone of the given frequency and length to waveform."""
        angular = 2.0 * math.pi * frequency / self._sample_rate
        gain = max(0.0, min(1.0, volume))
        for n in range(sample_count):
            waveform.append(gain * math.sin(angular * n))
