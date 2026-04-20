"""Accessible Spatial Audio Terminal.

Phase 1 added the foundational data structures and event bus.
Phase 2 added the execution kernel and its subprocess runner.
Phase 3 added the audio building blocks: TTS abstraction, synthetic
and measured HRTFs, convolution-based spatialization, and pluggable
sinks.
Phase 4 added the input and state router: abstract Key value type,
NotebookCursor for non-visual navigation between cells, and an
InputRouter that dispatches keystrokes to focus-aware actions.
Phase 5 added output parsing and the contextual action system:
OutputBuffer and OutputRecorder capture streamed lines per cell,
OutputCursor walks them line-by-line, and the ActionCatalog /
ActionMenu pair exposes focus-driven affordances such as copying a
line, copying stderr, or returning to the notebook.
Phase 6 adds the ANSI / interactive TUI mapping layer: an AnsiParser
tokenizes raw program output, VirtualScreen replays the tokens onto a
grid, the interactive-menu detector extracts a structured menu model
from the grid, and TuiBridge glues them together so a live TUI stream
emits INTERACTIVE_MENU_DETECTED / UPDATED / CLEARED events that the
audio engine can voice natively.
Phase A (Audio) replaces the Phase-3 hard-coded voice router with a
data-driven stack: SoundBank carries voices, sound recipes, and
event bindings; SoundGeneratorRegistry synthesises non-speech cues;
SoundEngine dispatches bindings to audio output; SettingsEditor plus
SettingsController let users tune the bank live from the keyboard.
"""

from asat.actions import (
    ActionCatalog,
    ActionContext,
    ActionMenu,
    Clipboard,
    MemoryClipboard,
    MenuItem,
    SystemClipboard,
    default_actions,
)
from asat.app import Application
from asat.ansi import (
    AnsiParser,
    CSIToken,
    ControlToken,
    EscapeToken,
    OSCToken,
    TextToken,
    Token,
)
from asat.audio import (
    AudioBuffer,
    ChannelLayout,
    DEFAULT_SAMPLE_RATE,
    SpatialPosition,
    VoicePreset,
    VoiceProfile,
)
from asat.audio_sink import (
    AudioSink,
    LiveAudioUnavailable,
    MemorySink,
    PosixLiveAudioSink,
    SoundDeviceSink,
    WavFileSink,
    WindowsLiveAudioSink,
    buffer_to_wav_bytes,
    pick_live_sink,
    write_wav,
)
from asat.cell import Cell, CellStatus
from asat.default_bank import COVERED_EVENT_TYPES, default_sound_bank
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.hrtf import HRTFProfile, Spatializer, convolve
from asat.input_router import BindingMap, InputRouter, default_bindings
from asat.interactive import InteractiveMenu, MenuItemView, detect
from asat.kernel import ExecutionKernel
from asat.keyboard import (
    KeyboardNotAvailable,
    KeyboardReader,
    PosixKeyboard,
    ScriptedKeyboard,
    WindowsKeyboard,
    pick_default as pick_default_keyboard,
)
from asat.keys import Key, Modifier
from asat.notebook import FocusMode, FocusState, NotebookCursor
from asat.onboarding import DEFAULT_ONBOARDING_LINES, OnboardingCoordinator
from asat.prompt_context import PromptContext
from asat.output_buffer import OutputBuffer, OutputLine, OutputRecorder
from asat.output_cursor import OutputCursor
from asat.runner import ProcessRunner
from asat.screen import Cell as ScreenCell, ScreenSnapshot, VirtualScreen
from asat.session import Session
from asat.terminal import TerminalRenderer
from asat.settings_controller import SettingsController
from asat.settings_editor import SettingsEditor
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice
from asat.sound_engine import SoundEngine
from asat.sound_generators import SoundGeneratorRegistry, generate_sound
from asat.tts import ToneTTSEngine, TTSEngine
from asat.tui_bridge import TuiBridge

__all__ = [
    "ActionCatalog",
    "ActionContext",
    "ActionMenu",
    "AnsiParser",
    "Application",
    "AudioBuffer",
    "AudioSink",
    "BindingMap",
    "CSIToken",
    "COVERED_EVENT_TYPES",
    "Cell",
    "CellStatus",
    "ChannelLayout",
    "Clipboard",
    "ControlToken",
    "DEFAULT_ONBOARDING_LINES",
    "DEFAULT_SAMPLE_RATE",
    "EscapeToken",
    "Event",
    "EventBinding",
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
    "InteractiveMenu",
    "Key",
    "KeyboardNotAvailable",
    "KeyboardReader",
    "LiveAudioUnavailable",
    "MemoryClipboard",
    "MemorySink",
    "MenuItem",
    "MenuItemView",
    "Modifier",
    "NotebookCursor",
    "OSCToken",
    "OnboardingCoordinator",
    "OutputBuffer",
    "OutputCursor",
    "OutputLine",
    "OutputRecorder",
    "PosixKeyboard",
    "PosixLiveAudioSink",
    "ProcessRunner",
    "PromptContext",
    "ScreenCell",
    "ScreenSnapshot",
    "ScriptedKeyboard",
    "Session",
    "SettingsController",
    "SettingsEditor",
    "SoundBank",
    "SoundDeviceSink",
    "SoundEngine",
    "SoundGeneratorRegistry",
    "SoundRecipe",
    "SpatialPosition",
    "Spatializer",
    "SystemClipboard",
    "TTSEngine",
    "TerminalRenderer",
    "TextToken",
    "Token",
    "ToneTTSEngine",
    "TuiBridge",
    "VirtualScreen",
    "Voice",
    "VoicePreset",
    "VoiceProfile",
    "WavFileSink",
    "WindowsKeyboard",
    "WindowsLiveAudioSink",
    "buffer_to_wav_bytes",
    "convolve",
    "default_actions",
    "default_bindings",
    "default_sound_bank",
    "detect",
    "generate_sound",
    "pick_default_keyboard",
    "pick_live_sink",
    "write_wav",
]

__version__ = "0.7.0"
