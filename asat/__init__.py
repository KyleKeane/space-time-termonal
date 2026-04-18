"""Accessible Spatial Audio Terminal.

Phase 1 added the foundational data structures and event bus.
Phase 2 adds the execution kernel and its supporting subprocess
runner. Later phases bring the audio engine, input router, output
parser, and ANSI interactivity layer.
"""

from asat.cell import Cell, CellStatus
from asat.session import Session
from asat.events import Event, EventType
from asat.event_bus import EventBus
from asat.execution import ExecutionMode, ExecutionRequest, ExecutionResult
from asat.runner import ProcessRunner
from asat.kernel import ExecutionKernel

__all__ = [
    "Cell",
    "CellStatus",
    "Session",
    "Event",
    "EventType",
    "EventBus",
    "ExecutionKernel",
    "ExecutionMode",
    "ExecutionRequest",
    "ExecutionResult",
    "ProcessRunner",
]

__version__ = "0.2.0"
