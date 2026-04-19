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
from typing import Callable, Optional, TextIO

from asat.actions import (
    ActionCatalog,
    ActionMenu,
    Clipboard,
    MemoryClipboard,
    default_actions,
)
from asat.audio_sink import AudioSink, MemorySink
from asat.default_bank import default_sound_bank
from asat.error_tail import StderrTailAnnouncer
from asat.completion_alert import CompletionFocusWatcher
from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.execution_worker import ExecutionWorker
from asat.input_router import InputRouter, default_bindings
from asat.jsonl_logger import JsonlEventLogger
from asat.kernel import ExecutionKernel
from asat.keys import Key
from asat.runner import ProcessRunner
from asat.notebook import FocusMode, NotebookCursor
from asat.onboarding import OnboardingCoordinator
from asat.output_buffer import OutputRecorder
from asat.output_cursor import OutputCursor
from asat.prompt_context import PromptContext
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
    runner: object  # ProcessRunner or ShellBackend; both expose `.run(...)`.
    router: InputRouter
    recorder: OutputRecorder
    output_cursor: OutputCursor
    sound_engine: SoundEngine
    sink: AudioSink
    settings_controller: SettingsController
    clipboard: Clipboard
    action_catalog: ActionCatalog
    action_menu: ActionMenu
    prompt_context: PromptContext
    error_tail: StderrTailAnnouncer
    completion_watcher: CompletionFocusWatcher
    onboarding: Optional[OnboardingCoordinator] = None
    event_logger: Optional[JsonlEventLogger] = None
    session_path: Optional[Path] = None
    # F62: when async_execution=True, `execute(cell_id)` hands the id
    # to this worker's background queue instead of running on the
    # caller's thread. None means synchronous execution (the default
    # for tests and library embeddings that expect deterministic
    # in-line ordering).
    execution_worker: Optional[ExecutionWorker] = None
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
        clipboard_factory: Optional[
            "Callable[[EventBus], Clipboard]"
        ] = None,
        onboarding_factory: Optional[
            "Callable[[EventBus], OnboardingCoordinator]"
        ] = None,
        log_factory: Optional[
            "Callable[[EventBus], JsonlEventLogger]"
        ] = None,
        runner: Optional[object] = None,
        async_execution: bool = False,
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

        `clipboard_factory` is the hook the CLI uses to install a
        `SystemClipboard` (so "copy output line" lands on the real OS
        clipboard) without forcing the in-process `MemoryClipboard`
        default on every test. The factory receives the freshly built
        `EventBus` so adapters can publish warnings.

        `onboarding_factory` hooks in an `OnboardingCoordinator` that
        fires a one-time welcome tour the first time ASAT runs on a
        machine. The factory receives the bus and is expected to
        return a configured coordinator; `Application.build` invokes
        `.run()` after the session banner publishes, so the greeting
        lands after the newcomer knows the session is alive. Tests
        and `--quiet` mode leave this unset, which skips onboarding
        entirely.

        `log_factory` attaches a `JsonlEventLogger` (F22) before any
        startup event fires so the diagnostic log captures the full
        session including `SESSION_CREATED` and the initial
        `FOCUS_CHANGED`. Tests and default invocations leave it unset.

        `runner` is the execution backend the kernel routes every cell
        through. `None` (the default) builds a fresh `ProcessRunner` —
        the per-cell `subprocess.Popen` model. Pass a `ShellBackend`
        instance to give the whole session one long-lived shell so
        `cd`, `export`, function definitions, and shell options carry
        between cells (F60). The Application owns the lifecycle either
        way and calls `runner.close()` (when present) from `close()`.

        `async_execution` (F62) switches `execute(cell_id)` from
        synchronous to a background queue. When True, a dedicated
        daemon thread pulls cell ids one at a time and feeds them to
        the kernel, so submissions made while a prior cell is still
        running just land in the queue instead of freezing the
        keyboard read. Tests and embedding code that rely on
        deterministic inline ordering leave it False; the CLI flips
        it on.
        """
        bus = EventBus()
        # Attach the diagnostic logger FIRST so SESSION_CREATED and the
        # very first FOCUS_CHANGED land in the jsonl file.
        event_logger = log_factory(bus) if log_factory is not None else None
        seeded = session is None
        resolved_session = session if session is not None else Session.new()
        cursor = NotebookCursor(resolved_session, bus)
        resolved_runner = runner if runner is not None else ProcessRunner()
        kernel = ExecutionKernel(bus, runner=resolved_runner)
        recorder = OutputRecorder(bus)
        output_cursor = OutputCursor(bus)
        resolved_bank = bank if bank is not None else default_sound_bank()
        settings_controller = SettingsController(
            bus,
            resolved_bank,
            save_path=bank_path,
            defaults_bank=default_sound_bank(),
        )
        if clipboard_factory is None:
            clipboard: Clipboard = MemoryClipboard()
        else:
            clipboard = clipboard_factory(bus)
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
            output_recorder=recorder,
        )
        resolved_sink: AudioSink = sink if sink is not None else MemorySink()
        sound_engine = SoundEngine(bus, resolved_bank, resolved_sink)
        # PromptContext must subscribe BEFORE TerminalRenderer so that
        # when the user transitions into INPUT mode post-command, the
        # PROMPT_REFRESH event it publishes reaches the renderer in
        # dispatch order (helpful for test assertions that check the
        # rendered line sequence).
        prompt_context = PromptContext(bus)
        # error_tail subscribes AFTER sound_engine so the normal
        # failure chord + narration play first; the stderr-tail
        # announcement arrives a beat later.
        error_tail = StderrTailAnnouncer(bus, recorder)
        # completion_watcher subscribes to FOCUS_CHANGED before the
        # first focus event fires (via cursor.new_cell below) so the
        # shadow focus is never empty when a command completes (F34).
        completion_watcher = CompletionFocusWatcher(bus)
        if text_trace is not None:
            TerminalRenderer(bus, stream=text_trace)
        onboarding = onboarding_factory(bus) if onboarding_factory is not None else None
        if async_execution:
            execution_worker: Optional[ExecutionWorker] = ExecutionWorker(
                bus, kernel, resolved_session
            )
            execution_worker.start()
        else:
            execution_worker = None
        app = cls(
            bus=bus,
            session=resolved_session,
            cursor=cursor,
            kernel=kernel,
            runner=resolved_runner,
            router=router,
            recorder=recorder,
            output_cursor=output_cursor,
            sound_engine=sound_engine,
            sink=resolved_sink,
            settings_controller=settings_controller,
            clipboard=clipboard,
            action_catalog=action_catalog,
            action_menu=action_menu,
            prompt_context=prompt_context,
            error_tail=error_tail,
            completion_watcher=completion_watcher,
            onboarding=onboarding,
            event_logger=event_logger,
            session_path=Path(session_path) if session_path is not None else None,
            execution_worker=execution_worker,
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
        if onboarding is not None:
            onboarding.run()
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
        """Run the cell with the given id through the execution kernel.

        When `async_execution=True` was passed to `build`, this hands
        the id off to the background worker and returns immediately —
        the caller never blocks waiting for the command to finish.
        Otherwise the call runs synchronously on the caller's thread,
        matching pre-F62 behaviour.
        """
        if self.execution_worker is not None:
            self.execution_worker.enqueue(cell_id)
            return
        cell = self.session.get_cell(cell_id)
        self.kernel.execute(cell)

    def close(self) -> None:
        """Flush the sink and persist the session if a path was given."""
        # Stop the worker BEFORE closing the runner so any cell still
        # in-flight finishes against a live shell instead of racing a
        # torn-down backend.
        if self.execution_worker is not None:
            self.execution_worker.close()
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
        # `ShellBackend` owns a long-lived process; `ProcessRunner` has
        # nothing to clean up and so doesn't define `close`.
        runner_close = getattr(self.runner, "close", None)
        if callable(runner_close):
            runner_close()
        if self.event_logger is not None:
            self.event_logger.close()

    def _on_action_invoked(self, event: Event) -> None:
        """Capture cell submissions and meta-commands for the driver."""
        payload = event.payload
        action = payload.get("action")
        if action == "repeat_last_narration":
            self.sound_engine.replay_last_narration()
            return
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
            elif meta == "welcome":
                self._replay_welcome()
            elif meta == "repeat":
                self.sound_engine.replay_last_narration()

    def _replay_welcome(self) -> None:
        """Re-invoke the onboarding tour on `:welcome` (F44).

        Re-publishes `FIRST_RUN_DETECTED` with `replay=True` through
        the same coordinator that ran at launch, so every existing
        subscriber (audio bank, renderer) reacts just as it did on
        the first boot. Silently no-ops when onboarding was
        disabled (`--quiet`, `--check`) so the meta-command stays a
        harmless tab-completion hit in those modes.
        """
        if self.onboarding is None:
            return
        self.onboarding.run(force=True)

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
