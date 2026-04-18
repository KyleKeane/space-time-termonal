"""Accessible Spatial Audio Terminal.

Phase 1 added the foundational data structures and event bus.
Phase 2 added the execution kernel and its subprocess runner.
Phase 3 added the spatial audio engine: TTS abstraction, synthetic
and measured HRTFs, convolution-based spatialization, and pluggable
sinks.
Phase 4 adds the input and state router: abstract Key value type,
NotebookCursor for non-visual navigation between cells, and an
InputRouter that dispatches keystrokes to focus-aware actions.
Later phases bring the output parser and ANSI interactivity layer.
"""

from asat.audio import (
    AudioBuffer,
    ChannelLayout,
    DEFAULT_SAMPLE_RATE,
    SpatialPosition,
    VoicePreset,
    VoiceProfile,
)
from asat.audio_engine import SpatialAudioEngine, VoiceRouter
from asat.audio_sink import AudioSink, MemorySink, WavFileSink, write_wav
from asat.cell import Cell, CellStatus
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.hrtf import HRTFProfile, Spatializer, convolve
from asat.input_router import BindingMap, InputRouter, default_bindings
from asat.kernel import ExecutionKernel
from asat.keys import Key, Modifier
from asat.notebook import FocusMode, FocusState, NotebookCursor
from asat.runner import ProcessRunner
from asat.session import Session
from asat.tts import ToneTTSEngine, TTSEngine

__all__ = [
    "AudioBuffer",
    "AudioSink",
    "BindingMap",
    "Cell",
    "CellStatus",
    "ChannelLayout",
    "DEFAULT_SAMPLE_RATE",
    "Event",
    "EventBus",
    "EventType",
    "ExecutionKernel",
    "ExecutionMode",
    "ExecutionRequest",
    "ExecutionResult",
    "FocusMode",
    "FocusState",
    "HRTFProfile",
    "InputRouter",
    "Key",
    "MemorySink",
    "Modifier",
    "NotebookCursor",
    "ProcessRunner",
    "Session",
    "SpatialAudioEngine",
    "SpatialPosition",
    "Spatializer",
    "TTSEngine",
    "ToneTTSEngine",
    "VoicePreset",
    "VoiceProfile",
    "VoiceRouter",
    "WavFileSink",
    "convolve",
    "default_bindings",
    "write_wav",
]

__version__ = "0.4.0"
