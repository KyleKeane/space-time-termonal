"""Accessible Spatial Audio Terminal.

Phase 1 exposes the foundational data structures and event bus.
Later phases add the execution kernel, audio engine, input router,
output parser, and ANSI interactivity layer.
"""

from asat.cell import Cell, CellStatus
from asat.session import Session
from asat.events import Event, EventType
from asat.event_bus import EventBus

__all__ = [
    "Cell",
    "CellStatus",
    "Session",
    "Event",
    "EventType",
    "EventBus",
]

__version__ = "0.1.0"
