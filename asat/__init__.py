"""Accessible Spatial Audio Terminal.

Phase 1 added the foundational data structures and event bus.
Phase 2 added the execution kernel and its subprocess runner.
Phase 3 added the spatial audio engine: TTS abstraction, synthetic
and measured HRTFs, convolution-based spatialization, and pluggable
sinks.
Phase 4 added the input and state router: abstract Key value type,
NotebookCursor for non-visual navigation between cells, and an
InputRouter that dispatches keystrokes to focus-aware actions.
Phase 5 adds output parsing and the contextual action system:
OutputBuffer and OutputRecorder capture streamed lines per cell,
OutputCursor walks them line-by-line, and the ActionCatalog /
ActionMenu pair exposes focus-driven affordances such as copying a
line, copying stderr, or returning to the notebook.
The ANSI interactivity layer will follow in a later phase.
"""

from asat.actions import (
    ActionCatalog,
    ActionContext,
    ActionMenu,
    Clipboard,
    MemoryClipboard,
    MenuItem,
    default_actions,
)
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
from asat.output_buffer import OutputBuffer, OutputLine, OutputRecorder
from asat.output_cursor import OutputCursor
from asat.runner import ProcessRunner
from asat.session import Session
from asat.tts import ToneTTSEngine, TTSEngine

__all__ = [
    "ActionCatalog",
    "ActionContext",
    "ActionMenu",
    "AudioBuffer",
    "AudioSink",
    "BindingMap",
    "Cell",
    "CellStatus",
    "ChannelLayout",
    "Clipboard",
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
    "MemoryClipboard",
    "MemorySink",
    "MenuItem",
    "Modifier",
    "NotebookCursor",
    "OutputBuffer",
    "OutputCursor",
    "OutputLine",
    "OutputRecorder",
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
    "default_actions",
    "default_bindings",
    "write_wav",
]

__version__ = "0.5.0"
