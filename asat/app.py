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

import os
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
from asat.event_log import EventLogViewer
from asat.event_log_file import EventLogFile
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
from asat.output_playback import OutputPlaybackDriver
from asat.prompt_context import PromptContext
from asat.session import Session
from asat.settings_controller import SettingsController
from asat.sound_bank import SoundBank
from asat.sound_engine import SoundEngine
from asat.tts import TTSEngine
from asat.tts_registry import TTSEngineRegistry, TTSRegistryError
from asat.streaming_monitor import StreamingMonitor
from asat.terminal import TerminalRenderer
from asat.workspace import Workspace, WorkspaceError


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
    streaming_monitor: Optional[StreamingMonitor] = None
    output_playback: Optional[OutputPlaybackDriver] = None
    onboarding: Optional[OnboardingCoordinator] = None
    event_logger: Optional[JsonlEventLogger] = None
    event_log_viewer: Optional[EventLogViewer] = None
    event_log_file: Optional[EventLogFile] = None
    session_path: Optional[Path] = None
    # F50: when an Application is opened from a workspace, this holds
    # the Workspace handle so meta-commands (`:workspace`,
    # `:list-notebooks`, `:new-notebook`) can resolve names against
    # the project root and so `close()` can update the
    # `last_opened_notebook` pointer in `<workspace>/.asat/config.json`.
    # ``None`` means the legacy single-notebook path (`asat --session
    # foo.json`); every other surface ignores the field.
    workspace: Optional[Workspace] = None
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
        # Per-session TTS state for the `:tts` meta-command. The
        # registry is the single source of engine-id → factory; the
        # id + parameter dict remember how the current engine was
        # built so `:tts set` can rebuild with one parameter changed.
        self._tts_registry: TTSEngineRegistry = TTSEngineRegistry.default()
        self._tts_engine_id: Optional[str] = _classify_tts_engine(
            self.sound_engine.tts, self._tts_registry
        )
        self._tts_parameters: dict[str, object] = {}
        self.bus.subscribe(EventType.ACTION_INVOKED, self._on_action_invoked)
        self.bus.subscribe(EventType.FOCUS_CHANGED, self._on_focus_changed)

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
        workspace: Optional[Workspace] = None,
        tts: Optional[TTSEngine] = None,
        show_outline: bool = False,
        show_trace: bool = True,
        event_log_dir: Optional[Path | str] = None,
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

        `workspace` (F50) attaches a Workspace so meta-commands and
        `close()` can read/write `<root>/.asat/config.json` and the
        per-project notebooks directory. When set, the constructor
        also chdirs into the resolved cwd (per-notebook
        ``Session.cwd`` if present, else workspace root) and
        publishes WORKSPACE_OPENED + NOTEBOOK_OPENED so the user
        hears which project they landed in. Pass ``None`` for the
        legacy single-file mode where ``--session foo.json`` is the
        whole story.
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
        resolved_sink: AudioSink = sink if sink is not None else MemorySink()
        sound_engine = SoundEngine(bus, resolved_bank, resolved_sink, tts=tts)
        # F39: the viewer subscribes to `*` up front so every event
        # from here on — including the launch banner — lands in its
        # ring buffer. Constructing it before the InputRouter lets the
        # router wire Ctrl+E / :log to a real collaborator.
        event_log_viewer = EventLogViewer(bus, sound_engine)
        # F63: the grouped text file logger writes under
        # `<workspace>/.asat/log/` when a workspace is attached, or
        # `event_log_dir` when the CLI asked for one explicitly. No
        # directory configured → no file logger (tests / one-shot
        # scripts stay silent on disk).
        resolved_event_log_dir = _resolve_event_log_dir(
            workspace, event_log_dir
        )
        event_log_file = (
            EventLogFile(bus, resolved_event_log_dir)
            if resolved_event_log_dir is not None
            else None
        )
        router = InputRouter(
            cursor,
            bus,
            bindings=default_bindings(),
            output_cursor=output_cursor,
            settings_controller=settings_controller,
            action_menu=action_menu,
            output_recorder=recorder,
            event_log_viewer=event_log_viewer,
        )
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
        # streaming_monitor (F37) subscribes alongside completion_watcher
        # so the silence / beat windows track every cell from the first
        # COMMAND_STARTED onward. The background ticker is a daemon
        # thread, safe to start unconditionally — tests still drive
        # `check()` with an injected clock and never observe the ticker.
        streaming_monitor = StreamingMonitor(bus)
        streaming_monitor.start_background_ticker()
        # F24 continuous playback: one driver per Application, bound to
        # the singleton `output_cursor`. The background ticker is a
        # daemon so tests that never tap playback never see a thread.
        output_playback = OutputPlaybackDriver(bus, output_cursor)
        output_playback.start_background_ticker()
        if text_trace is not None:
            TerminalRenderer(
                bus,
                stream=text_trace,
                show_trace=show_trace,
                show_outline=show_outline,
                cells_provider=(
                    (lambda: resolved_session.cells)
                    if show_outline
                    else None
                ),
                event_log_viewer=event_log_viewer,
            )
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
            streaming_monitor=streaming_monitor,
            output_playback=output_playback,
            onboarding=onboarding,
            event_logger=event_logger,
            event_log_viewer=event_log_viewer,
            event_log_file=event_log_file,
            session_path=Path(session_path) if session_path is not None else None,
            execution_worker=execution_worker,
            workspace=workspace,
        )
        # F50: chdir BEFORE the launch events so a `pwd` cell run as
        # the user's first action sees the project directory and so
        # `PROMPT_REFRESH` carries the right cwd from the very first
        # focus event.
        if workspace is not None:
            target = workspace.resolve_cwd(resolved_session)
            if target.exists():
                os.chdir(target)
        # Everything below fires AFTER sound_engine and (if requested)
        # the TerminalRenderer have subscribed, so the launch banner
        # both narrates through the sink and prints to the text trace.
        publish_event(
            bus,
            EventType.SESSION_CREATED,
            {"session_id": resolved_session.session_id},
            source="app",
        )
        if workspace is not None:
            publish_event(
                bus,
                EventType.WORKSPACE_OPENED,
                {
                    "root": str(workspace.root),
                    "name": workspace.root.name,
                    "notebook_count": len(workspace.list_notebooks()),
                },
                source="app",
            )
            if session_path is not None:
                publish_event(
                    bus,
                    EventType.NOTEBOOK_OPENED,
                    {
                        "path": str(session_path),
                        "name": Path(session_path).stem,
                    },
                    source="app",
                )
                workspace.set_last_opened(Path(session_path))
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
        # F43 + PR 4: on the very first launch of ASAT, seed a
        # three-cell demo notebook (H1 + H2 + command) so the outline
        # pane renders on-screen, then run the scripted tour so a
        # newcomer hears what each surface sounds like. We check the
        # sentinel BEFORE `onboarding.run()` flips it, then publish
        # the beats AFTER the welcome fires so the audio order is:
        # welcome → "press Enter to run" → event log preview → log
        # path → tour completed.
        from asat.onboarding import (
            FIRST_RUN_OUTLINE_HEADINGS,
            FIRST_RUN_TOUR_COMMAND,
        )

        first_run_tour = (
            seeded
            and onboarding is not None
            and onboarding.is_first_run()
        )
        if seeded:
            if first_run_tour:
                for level, title in FIRST_RUN_OUTLINE_HEADINGS:
                    cursor.new_heading_cell(level, title)
                cursor.new_cell(FIRST_RUN_TOUR_COMMAND)
            else:
                cursor.new_cell("")
        if onboarding is not None:
            onboarding.run()
        if first_run_tour and onboarding is not None:
            app._run_scripted_tour(replay=False)
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

        Non-executable cells (F61 heading landmarks, future read-only
        kinds) are silently skipped so an accidental stray id cannot
        crash the driver — the router paths already guard against
        routing headings here, this is belt-and-braces.
        """
        cell = self.session.get_cell(cell_id)
        if not cell.is_executable:
            return
        if self.execution_worker is not None:
            self.execution_worker.enqueue(cell_id)
            return
        self.kernel.execute(cell)

    def close(self) -> None:
        """Flush the sink and persist the session if a path was given."""
        # Stop the worker BEFORE closing the runner so any cell still
        # in-flight finishes against a live shell instead of racing a
        # torn-down backend.
        if self.execution_worker is not None:
            self.execution_worker.close()
        if self.streaming_monitor is not None:
            self.streaming_monitor.close()
        if self.output_playback is not None:
            self.output_playback.close()
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
        if self.event_log_file is not None:
            self.event_log_file.close()

    def _on_action_invoked(self, event: Event) -> None:
        """Capture cell submissions and meta-commands for the driver."""
        payload = event.payload
        action = payload.get("action")
        # F24: any action other than the toggle itself stops playback.
        # The action is still dispatched — stopping only cancels the
        # auto-advance timer, letting the user's keystroke run its
        # normal course (e.g. Up / Down still move the line cursor).
        if (
            self.output_playback is not None
            and self.output_playback.active
            and action != "output_playback_toggle"
        ):
            self.output_playback.stop(reason="cancelled")
        if action == "repeat_last_narration":
            self.sound_engine.replay_last_narration()
            return
        if action == "cancel_command":
            self._cancel_running_command()
            return
        if action == "output_playback_toggle":
            self._toggle_output_playback()
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
            elif meta == "workspace":
                self._announce_workspace()
            elif meta == "list-notebooks":
                self._announce_notebook_list()
            elif meta == "new-notebook":
                self._create_notebook(str(payload.get("meta_argument", "")))
            elif meta == "verbosity":
                self._set_verbosity(str(payload.get("meta_argument", "")))
            elif meta == "reload-bank":
                self._reload_bank()
            elif meta == "tts":
                self._handle_meta_tts(str(payload.get("meta_argument", "")))

    def _cancel_running_command(self) -> None:
        """`cancel_command` (Ctrl+C in INPUT mode) — F1.

        Routes through `kernel.cancel(active_cell_id)`, which signals
        the runner and ensures the post-run path emits
        `COMMAND_CANCELLED` instead of `COMMAND_FAILED`. When no cell
        is currently running, surfaces a `HELP_REQUESTED` hint so the
        user hears why nothing happened — silently no-oping would
        leave them wondering whether the keystroke registered.
        """
        cell_id = self.kernel.active_cell_id
        if cell_id is None:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": ["No command is currently running."]},
                source="app",
            )
            return
        self.kernel.cancel(cell_id)

    def _toggle_output_playback(self) -> None:
        """`p` / `Space` in OUTPUT mode — F24 continuous playback.

        Toggles the `OutputPlaybackDriver`. Starting forwards the
        currently focused cell id so the STARTED / STOPPED events
        carry enough context for a binding to narrate "playing output
        for cell two". When the driver refuses to start (buffer empty
        or cursor already on the last line), we surface a
        `HELP_REQUESTED` hint so the user hears why nothing happened.
        """
        if self.output_playback is None:
            return
        if self.output_playback.active:
            self.output_playback.stop(reason="cancelled")
            return
        cell_id = self.cursor.focus.cell_id
        if not self.output_playback.start(cell_id):
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": ["Nothing to play — output is empty or at the end."]},
                source="app",
            )

    def _on_focus_changed(self, event: Event) -> None:
        """Stop playback when focus leaves OUTPUT mode — F24."""
        if self.output_playback is None or not self.output_playback.active:
            return
        new_mode = event.payload.get("new_mode")
        if new_mode != FocusMode.OUTPUT.value:
            self.output_playback.stop(reason="focus_changed")

    def _replay_welcome(self) -> None:
        """Re-invoke the onboarding tour on `:welcome` (F44).

        Re-publishes `FIRST_RUN_DETECTED` with `replay=True` through
        the same coordinator that ran at launch, then re-fires the
        four PR 4 scripted beats so every existing subscriber (audio
        bank, renderer) reacts just as it did on the first boot.
        Seeding is NOT repeated — that would destroy the user's
        notebook. Silently no-ops when onboarding was disabled
        (`--quiet`, `--check`) so the meta-command stays a harmless
        tab-completion hit in those modes.
        """
        if self.onboarding is None:
            return
        self.onboarding.run(force=True)
        self._run_scripted_tour(replay=True)

    def _run_scripted_tour(self, *, replay: bool) -> None:
        """Publish the four post-welcome tour beats in order.

        Called in two places: Application.build on a genuine first
        run (`replay=False`) and `_replay_welcome` on `:welcome`
        (`replay=True`). The `replay` flag is forwarded to every
        beat so subscribers + tests can tell the two paths apart.

        The event-log path beat carries the live path from
        `event_log_file.current_path()` when a file logger is
        attached; otherwise an empty string tells the narration to
        skip the mention rather than invent a fictional path.
        """
        if self.onboarding is None:
            return
        self.onboarding.publish_tour_step(replay=replay)
        self.onboarding.publish_event_log_preview_beat(replay=replay)
        log_path = (
            str(self.event_log_file.current_path())
            if self.event_log_file is not None
            else None
        )
        self.onboarding.publish_log_path_beat(log_path, replay=replay)
        self.onboarding.publish_tour_completed_beat(replay=replay)

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

    def _announce_workspace(self) -> None:
        """`:workspace` — re-announce the active project root and notebook count.

        A no-op when ASAT was started without a workspace (legacy
        ``--session foo.json`` mode); the meta-command still parses
        cleanly so users can type it without crashing the router.
        Re-publishes WORKSPACE_OPENED rather than HELP_REQUESTED so
        the existing audio binding (default_bank.workspace_opened)
        narrates the answer in the same voice as the launch banner.
        """
        if self.workspace is None:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": ["No workspace open — run `asat <dir>` to attach one."]},
                source="app",
            )
            return
        publish_event(
            self.bus,
            EventType.WORKSPACE_OPENED,
            {
                "root": str(self.workspace.root),
                "name": self.workspace.root.name,
                "notebook_count": len(self.workspace.list_notebooks()),
            },
            source="app",
        )

    def _announce_notebook_list(self) -> None:
        """`:list-notebooks` — narrate every notebook in the workspace.

        Publishes NOTEBOOK_LISTED with both ``names`` (machine-friendly
        list of stems) and ``summary`` (the human-friendly sentence the
        sound bank narrates). When no workspace is attached the user
        gets a HELP_REQUESTED hint instead of silence so they know
        why nothing was announced.
        """
        if self.workspace is None:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": ["No workspace open — run `asat <dir>` to attach one."]},
                source="app",
            )
            return
        notebooks = self.workspace.list_notebooks()
        names = [path.stem for path in notebooks]
        if not names:
            summary = "no notebooks yet"
        elif len(names) == 1:
            summary = f"1 notebook: {names[0]}"
        else:
            summary = f"{len(names)} notebooks: {', '.join(names)}"
        publish_event(
            self.bus,
            EventType.NOTEBOOK_LISTED,
            {"names": names, "summary": summary},
            source="app",
        )

    def _create_notebook(self, argument: str) -> None:
        """`:new-notebook <name>` — create a fresh notebook on disk.

        The created notebook inherits the workspace root as its cwd.
        Narrates NOTEBOOK_CREATED so the user hears confirmation, plus
        a follow-up HELP_REQUESTED that explains the in-flight switch
        is deferred to F51 — for now they restart ASAT with
        ``asat <root> <name>`` to open it. A blank or invalid name
        emits HELP_REQUESTED instead of crashing the router.
        """
        if self.workspace is None:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": ["No workspace open — run `asat <dir>` to attach one."]},
                source="app",
            )
            return
        name = argument.strip()
        if not name:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        "`:new-notebook <name>` — name is required.",
                        "Example: `:new-notebook ideas`.",
                    ],
                },
                source="app",
            )
            return
        try:
            path = self.workspace.new_notebook(name)
        except WorkspaceError as exc:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": [f"Could not create notebook: {exc}"]},
                source="app",
            )
            return
        publish_event(
            self.bus,
            EventType.NOTEBOOK_CREATED,
            {"path": str(path), "name": path.stem},
            source="app",
        )
        publish_event(
            self.bus,
            EventType.HELP_REQUESTED,
            {
                "lines": [
                    f"Created notebook {path.stem}. "
                    "Restart ASAT with `asat "
                    f"{self.workspace.root} {path.stem}` to open it."
                ],
            },
            source="app",
        )

    def _set_verbosity(self, argument: str) -> None:
        """`:verbosity <level>` — swap the bank's F31 narration ceiling.

        Malformed arguments emit HELP_REQUESTED listing the allowed
        levels instead of crashing; the engine itself publishes
        VERBOSITY_CHANGED when the swap takes effect so the user
        hears the new preset through the default-bank binding.
        """
        from asat.sound_bank import SoundBankError, VERBOSITY_LEVELS

        level = argument.strip().lower()
        if not level:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        "`:verbosity <level>` — level is one of "
                        + ", ".join(VERBOSITY_LEVELS)
                        + ".",
                        f"Currently: {self.sound_engine.bank.verbosity_level}.",
                    ],
                },
                source="app",
            )
            return
        try:
            self.sound_engine.set_verbosity_level(level)
        except SoundBankError as exc:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": [str(exc)]},
                source="app",
            )

    def _reload_bank(self) -> None:
        """`:reload-bank` — F3: discard in-memory edits and re-read the bank.

        The live bank is swapped for whatever ``SoundBank.load()``
        parses from the configured ``bank_path``. Any edits the user
        made in this session that were never saved are lost.

        Surfaces HELP_REQUESTED when no bank file was configured (the
        run has nothing to reload from) or when the file cannot be
        parsed (corrupt JSON, broken references). On success publishes
        BANK_RELOADED so the default bank can narrate "settings
        reloaded from disk" and tests can assert the swap happened.
        """
        from asat.sound_bank import SoundBankError

        path = self.settings_controller.save_path
        if path is None:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": ["No bank path configured; nothing to reload."]},
                source="app",
            )
            return
        try:
            fresh = SoundBank.load(path)
        except FileNotFoundError:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": [f"Bank file not found: {path}"]},
                source="app",
            )
            return
        except SoundBankError as exc:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": [f"Could not reload bank: {exc}"]},
                source="app",
            )
            return
        if self.settings_controller.is_open:
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        "Close the settings editor before reloading the bank."
                    ]
                },
                source="app",
            )
            return
        self.sound_engine.set_bank(fresh)
        self.settings_controller.reload_from_disk(fresh)
        publish_event(
            self.bus,
            EventType.BANK_RELOADED,
            {
                "path": str(path),
                "binding_count": len(fresh.bindings),
            },
            source="app",
        )

    def _handle_meta_tts(self, argument: str) -> None:
        """Dispatch `:tts list | use <id> | set <param> <value>`.

        The command is the user-facing knob for the pluggable TTS
        registry (``asat/tts_registry.py``). All three sub-commands
        narrate their outcome via ``HELP_REQUESTED`` so the user
        always hears confirmation — no silent state changes.
        """
        parts = argument.strip().split(maxsplit=2)
        subcommand = parts[0].lower() if parts else "list"
        if subcommand == "list":
            description = self._tts_registry.describe()
            active = self._tts_engine_id or "(custom)"
            params_summary = (
                ", ".join(f"{k}={v}" for k, v in self._tts_parameters.items())
                or "(defaults)"
            )
            lines = [
                f"active: {active}; parameters: {params_summary}",
                *description.splitlines(),
                "Use `:tts use <id>` to switch, `:tts set <param> <value>` to tune.",
            ]
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": lines},
                source="app",
            )
            return
        if subcommand == "use":
            if len(parts) < 2:
                publish_event(
                    self.bus,
                    EventType.HELP_REQUESTED,
                    {
                        "lines": [
                            "`:tts use <id>` — id is one of: "
                            f"{', '.join(self._tts_registry.available_ids()) or 'none'}."
                        ]
                    },
                    source="app",
                )
                return
            engine_id = parts[1]
            try:
                engine = self._tts_registry.build(engine_id)
            except TTSRegistryError as exc:
                publish_event(
                    self.bus,
                    EventType.HELP_REQUESTED,
                    {"lines": [f"`:tts use {engine_id}` — {exc}"]},
                    source="app",
                )
                return
            self.sound_engine.set_tts(engine)
            self._tts_engine_id = engine_id
            self._tts_parameters = {}
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {"lines": [f"TTS engine switched to {engine_id}."]},
                source="app",
            )
            return
        if subcommand == "set":
            if self._tts_engine_id is None:
                publish_event(
                    self.bus,
                    EventType.HELP_REQUESTED,
                    {
                        "lines": [
                            "`:tts set` is only available for registry-managed "
                            "engines. Run `:tts use <id>` first."
                        ]
                    },
                    source="app",
                )
                return
            if len(parts) < 3:
                publish_event(
                    self.bus,
                    EventType.HELP_REQUESTED,
                    {"lines": ["`:tts set <param> <value>` — see `:tts list` for params."]},
                    source="app",
                )
                return
            param_name, raw_value = parts[1], parts[2]
            new_params = dict(self._tts_parameters)
            new_params[param_name] = _coerce_tts_value(raw_value)
            try:
                engine = self._tts_registry.build(
                    self._tts_engine_id, parameters=new_params
                )
            except TTSRegistryError as exc:
                publish_event(
                    self.bus,
                    EventType.HELP_REQUESTED,
                    {"lines": [f"`:tts set` failed: {exc}"]},
                    source="app",
                )
                return
            self.sound_engine.set_tts(engine)
            self._tts_parameters = new_params
            publish_event(
                self.bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        f"TTS {self._tts_engine_id}: set {param_name}={raw_value}."
                    ]
                },
                source="app",
            )
            return
        publish_event(
            self.bus,
            EventType.HELP_REQUESTED,
            {
                "lines": [
                    f"`:tts {subcommand}` is not recognised. Try `:tts list`, "
                    "`:tts use <id>`, or `:tts set <param> <value>`."
                ]
            },
            source="app",
        )


def _resolve_event_log_dir(
    workspace: Optional[Workspace], explicit: Optional[Path | str]
) -> Optional[Path]:
    """Pick the directory the grouped event-log file (F63) should use.

    Precedence: an explicit ``event_log_dir`` wins; else
    ``<workspace.root>/.asat/log`` when a workspace is attached; else
    None (no file logger). The directory is created by the
    ``EventLogFile`` constructor, so callers don't have to ensure it
    exists before calling.
    """
    if explicit is not None:
        return Path(explicit)
    if workspace is not None:
        return workspace.root / ".asat" / "log"
    return None


_TTS_CLASS_TO_ID: dict[str, str] = {
    "ToneTTSEngine": "tone",
    "Pyttsx3Engine": "pyttsx3",
    "EspeakNgEngine": "espeak-ng",
    "SystemSayEngine": "say",
}


def _classify_tts_engine(
    engine: TTSEngine, registry: TTSEngineRegistry
) -> Optional[str]:
    """Map a live TTS engine object back to its registry id, or None.

    Used to seed ``Application._tts_engine_id`` so the ``:tts`` meta-
    command knows which knob to rebuild when the user says ``:tts set
    <param> <value>``. Returns ``None`` for engines not registered
    (custom plug-ins passed via ``Application.build(tts=...)``), which
    makes ``:tts set`` refuse cleanly.
    """
    engine_id = _TTS_CLASS_TO_ID.get(type(engine).__name__)
    if engine_id is None or not registry.has(engine_id):
        return None
    return engine_id


def _coerce_tts_value(raw: str) -> object:
    """Parse a ``:tts set`` value into the most natural Python type.

    Integers and floats parse via ``int``/``float``; everything else
    stays a string. Keeping the grammar this small matches the rest
    of ASAT's meta-commands — there is no need for a full expression
    parser when ``voice en-us`` and ``rate 220`` cover the useful
    knobs.
    """
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
