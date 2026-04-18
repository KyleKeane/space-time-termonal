"""Application: assemble the ASAT pipeline into a runnable unit.

Every other module in this repo is a piece of the pipeline. This one
assembles them: one event bus, one session, one cursor, one kernel,
one router, one sound engine, one sink. Until this module existed,
a running ASAT terminal was the responsibility of "embedding code" —
which didn't exist. The test suite was the only thing that drove the
full graph.

The Application performs no I/O of its own. A driver (the CLI in
`asat/__main__.py` or a test) feeds keystrokes via `handle_key` and
calls `drain_pending` to pick up cells the user just submitted, then
calls `execute` to run each one. Keeping I/O out of this class keeps
the full pipeline unit-testable without a real keyboard or audio
device.

The constructor is intentionally tiny; use `Application.build(...)`
to construct a fully-wired instance with sensible defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TextIO

from asat.actions import (
    ActionCatalog,
    ActionMenu,
    Clipboard,
    MemoryClipboard,
    default_actions,
)
from asat.audio_sink import AudioSink, MemorySink
from asat.default_bank import default_sound_bank
from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.input_router import InputRouter, default_bindings
from asat.kernel import ExecutionKernel
from asat.keys import Key
from asat.notebook import FocusMode, NotebookCursor
from asat.output_buffer import OutputRecorder
from asat.output_cursor import OutputCursor
from asat.session import Session
from asat.settings_controller import SettingsController
from asat.sound_bank import SoundBank
from asat.sound_engine import SoundEngine
from asat.terminal import TerminalRenderer


@dataclass
class Application:
    """The assembled ASAT pipeline.

    Fields are public so tests and embedding code can reach into any
    collaborator. The Application itself holds the cross-cutting state
    that does not belong to any single collaborator: a list of cells
    awaiting execution, and a `running` flag the driver watches to
    decide when to exit the event loop.
    """

    bus: EventBus
    session: Session
    cursor: NotebookCursor
    kernel: ExecutionKernel
    router: InputRouter
    recorder: OutputRecorder
    output_cursor: OutputCursor
    sound_engine: SoundEngine
    sink: AudioSink
    settings_controller: SettingsController
    clipboard: Clipboard
    action_catalog: ActionCatalog
    action_menu: ActionMenu
    session_path: Optional[Path] = None
    running: bool = True

    def __post_init__(self) -> None:
        """Wire subscriptions that need the fully-constructed Application."""
        self._pending: list[str] = []
        self.bus.subscribe(EventType.ACTION_INVOKED, self._on_action_invoked)

    @classmethod
    def build(
        cls,
        *,
        sink: Optional[AudioSink] = None,
        bank: Optional[SoundBank] = None,
        bank_path: Optional[Path | str] = None,
        session: Optional[Session] = None,
        session_path: Optional[Path | str] = None,
        text_trace: Optional[TextIO] = None,
    ) -> "Application":
        """Wire every collaborator with sensible defaults.

        `sink` defaults to `MemorySink` so the build is safe in any
        environment. `bank` defaults to `default_sound_bank()`. A
        provided `bank_path` is remembered as the save target for the
        settings editor but is NOT loaded here — pass `bank=SoundBank.load(path)`
        explicitly if the caller wants that behaviour.

        When `session` is None, a new Session with one empty cell is
        created and the cursor lands in INPUT mode on it so the user
        can start typing immediately.

        When `text_trace` is a writable stream (e.g. `sys.stdout`),
        a `TerminalRenderer` is attached to the bus BEFORE the startup
        publishes fire, so the `[asat] session … ready.` banner and
        the initial `[input #…]` line are captured. Pass `None`
        (default) to suppress the text trace.
        """
        bus = EventBus()
        seeded = session is None
        resolved_session = session if session is not None else Session.new()
        cursor = NotebookCursor(resolved_session, bus)
        kernel = ExecutionKernel(bus)
        recorder = OutputRecorder(bus)
        output_cursor = OutputCursor(bus)
        resolved_bank = bank if bank is not None else default_sound_bank()
        settings_controller = SettingsController(
            bus,
            resolved_bank,
            save_path=bank_path,
        )
        clipboard: Clipboard = MemoryClipboard()
        action_catalog = default_actions(
            cursor=cursor,
            recorder=recorder,
            output_cursor=output_cursor,
            clipboard=clipboard,
            bus=bus,
        )
        action_menu = ActionMenu(bus, action_catalog)
        router = InputRouter(
            cursor,
            bus,
            bindings=default_bindings(),
            output_cursor=output_cursor,
            settings_controller=settings_controller,
            action_menu=action_menu,
        )
        resolved_sink: AudioSink = sink if sink is not None else MemorySink()
        sound_engine = SoundEngine(bus, resolved_bank, resolved_sink)
        if text_trace is not None:
            TerminalRenderer(bus, stream=text_trace)
        app = cls(
            bus=bus,
            session=resolved_session,
            cursor=cursor,
            kernel=kernel,
            router=router,
            recorder=recorder,
            output_cursor=output_cursor,
            sound_engine=sound_engine,
            sink=resolved_sink,
            settings_controller=settings_controller,
            clipboard=clipboard,
            action_catalog=action_catalog,
            action_menu=action_menu,
            session_path=Path(session_path) if session_path is not None else None,
        )
        # Everything below fires AFTER sound_engine and (if requested)
        # the TerminalRenderer have subscribed, so the launch banner
        # both narrates through the sink and prints to the text trace.
        publish_event(
            bus,
            EventType.SESSION_CREATED,
            {"session_id": resolved_session.session_id},
            source="app",
        )
        if not seeded and session_path is not None:
            publish_event(
                bus,
                EventType.SESSION_LOADED,
                {
                    "session_id": resolved_session.session_id,
                    "path": str(session_path),
                },
                source="app",
            )
        if seeded:
            cursor.new_cell()
        return app

    def handle_key(self, key: Key) -> Optional[str]:
        """Dispatch a keystroke and return the action name, if any.

        A `submit` action may enqueue a cell for execution; the caller
        picks those up via `drain_pending` and hands them to `execute`.
        """
        return self.router.handle_key(key)

    def drain_pending(self) -> list[str]:
        """Return and clear the list of cell ids awaiting execution."""
        pending = self._pending
        self._pending = []
        return pending

    def execute(self, cell_id: str) -> None:
        """Run the cell with the given id through the execution kernel."""
        cell = self.session.get_cell(cell_id)
        self.kernel.execute(cell)

    def close(self) -> None:
        """Flush the sink and persist the session if a path was given."""
        if self.session_path is not None:
            self.session.save(self.session_path)
            publish_event(
                self.bus,
                EventType.SESSION_SAVED,
                {
                    "session_id": self.session.session_id,
                    "path": str(self.session_path),
                },
                source="app",
            )
        self.sink.close()

    def _on_action_invoked(self, event: Event) -> None:
        """Capture cell submissions and meta-commands for the driver."""
        payload = event.payload
        action = payload.get("action")
        if action == "submit":
            cell_id = payload.get("cell_id")
            command = payload.get("command", "")
            is_meta = payload.get("meta_command") is not None
            if cell_id is not None and not is_meta and str(command).strip():
                self._pending.append(str(cell_id))
            meta = payload.get("meta_command")
            if meta == "quit":
                self.running = False
            elif meta == "save":
                self._save_session()

    def _save_session(self) -> None:
        """Persist the session to `session_path` if one was configured.

        With no `--session` path given, `:save` has nothing to persist
        — the `session saved` audio cue still fires so the user hears
        that their input was received.
        """
        if self.session_path is None:
            return
        self.session.save(self.session_path)
        publish_event(
            self.bus,
            EventType.SESSION_SAVED,
            {
                "session_id": self.session.session_id,
                "path": str(self.session_path),
            },
            source="app",
        )
