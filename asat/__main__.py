"""Launch the Accessible Spatial Audio Terminal from the command line.

`python -m asat` assembles an Application with sensible defaults and
drives it from real keystrokes. When stdout is a terminal and no
audio flag was passed, the CLI opts into live audio automatically
(``--live``) so a first-time user hears speech without reading any
docs. Piped / captured output keeps the safe ``MemorySink`` default.

    --live / --no-live  explicitly opt into or out of the platform
                        live-speaker sink. ``--live`` on a host with
                        no backend falls back to MemorySink with an
                        explanation.
    --wav-dir DIR       additionally write every rendered buffer to a
                        numbered WAV file under DIR.
    --tts ENGINE_ID     pick a TTS backend by id (``pyttsx3``,
                        ``espeak-ng``, ``say``, ``tone``). Omit to
                        auto-select the first available engine in
                        priority order. Tests pin ``tone``.
    --quiet             suppress the text trace on stdout (audio only).

Exit with the `:quit` meta-command (type `:quit` in INPUT mode then
press Enter) or by sending EOF (Ctrl+D on POSIX, Ctrl+Z then Enter on
Windows). The full keystroke cheat sheet lives in
docs/USER_MANUAL.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from asat import __version__
from asat.actions import SystemClipboard
from asat.app import Application
from asat.audio_sink import (
    AudioSink,
    LiveAudioUnavailable,
    MemorySink,
    WavFileSink,
    pick_live_sink,
)
from asat.jsonl_logger import JsonlEventLogger
from asat.keyboard import KeyboardNotAvailable, KeyboardReader, pick_default
from asat.onboarding import OnboardingCoordinator
from asat.runner import ProcessRunner
from asat.self_check import run_self_check
from asat.session import Session
from asat.shell_backend import shell_backend_or_none
from asat.sound_bank import SoundBank
from asat.tts import TTSEngine
from asat.tts_registry import TTSEngineRegistry, TTSRegistryError
from asat.workspace import (
    WORKSPACE_NOTEBOOK_EXTENSION,
    Workspace,
    WorkspaceError,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse args, build the Application, and drive the read-dispatch loop."""
    args = _parse_args(argv)
    if args.version:
        print(f"asat {__version__}")
        return 0
    try:
        workspace, workspace_session_path = _resolve_workspace(args)
    except _FriendlyExit as exc:
        print(f"[asat] {exc}", file=sys.stderr)
        return 2
    if workspace_session_path is not None:
        # The positional / --init-workspace path supplies its own
        # session path; --session is incompatible with it.
        if args.session is not None:
            print(
                "[asat] --session cannot be combined with a workspace; "
                "open the notebook by name instead.",
                file=sys.stderr,
            )
            return 2
        args.session = workspace_session_path
    registry = TTSEngineRegistry.default()
    try:
        tts_engine = _resolve_tts(registry, args.tts)
    except TTSRegistryError as exc:
        print(f"[asat] --tts: {exc}", file=sys.stderr)
        return 2
    effective_live = _resolve_live_preference(args)
    sink = _make_sink(args.wav_dir, effective_live, quiet=args.quiet)
    if (
        not args.quiet
        and not args.check
        and not args.live
        and args.wav_dir is None
        and isinstance(sink, MemorySink)
    ):
        # Tell first-time users why they are hearing silence without
        # forcing them to read the docs. Suppressed when --live was
        # requested (the `--live unavailable` line already explained
        # why audio isn't reaching speakers) or when --check is
        # printing its own diagnostic report.
        print(
            "[asat] audio is going to the in-memory sink. Pass --live "
            "or --wav-dir DIR to hear or capture it. "
            "On Linux install a player (apt install pulseaudio-utils OR "
            "alsa-utils) and a TTS engine (pip install pyttsx3 OR "
            "apt install espeak-ng).",
            file=sys.stderr,
        )
    try:
        bank = _load_bank(args.bank)
    except _FriendlyExit as exc:
        print(f"[asat] {exc}", file=sys.stderr)
        return 2
    session = _load_session(args.session)
    # `--check` prints its own diagnostic report and must not mix the
    # TerminalRenderer trace into stdout. Every other invocation wants
    # the renderer attached BEFORE `Application.build` publishes
    # `SESSION_CREATED`/`FOCUS_CHANGED`, so the launch banner and the
    # first `[input #…]` line reach the user.
    trace_stream = None if args.quiet or args.check else sys.stdout
    view_mode = _resolve_view_mode(args)
    show_trace_pane = view_mode in ("trace", "both")
    show_outline_pane = view_mode in ("outline", "both")
    # A user who asked for `--live` or `--wav-dir` has told us where
    # audio goes; anything else is the silent MemorySink path F41
    # guards against.
    has_live_audio = bool(args.live) or args.wav_dir is not None
    onboarding_factory = _onboarding_factory(
        quiet=args.quiet, check=args.check, has_live_audio=has_live_audio
    )
    log_factory = _log_factory(args.log)
    runner = _pick_runner(no_shared_shell=args.no_shared_shell, quiet=args.quiet)
    # The `--check` path builds the Application only to read its
    # state; running the async worker there would pointlessly spawn a
    # thread. Every other CLI invocation flips the F62 queue on so a
    # slow command never freezes the keyboard read.
    async_execution = not args.check
    app = Application.build(
        sink=sink,
        bank=bank,
        bank_path=args.bank,
        session=session,
        session_path=args.session,
        text_trace=trace_stream,
        clipboard_factory=SystemClipboard,
        onboarding_factory=onboarding_factory,
        log_factory=log_factory,
        runner=runner,
        async_execution=async_execution,
        workspace=workspace,
        tts=tts_engine,
        show_trace=show_trace_pane,
        show_outline=show_outline_pane,
    )
    if args.check:
        exit_code = _print_check_report(app, args)
        app.close()
        return exit_code
    try:
        keyboard: KeyboardReader = pick_default()
    except KeyboardNotAvailable as exc:
        print(f"[asat] cannot start: {exc}", file=sys.stderr)
        app.close()
        return 2
    try:
        _run(app, keyboard)
    except KeyboardInterrupt:
        # Clean exit on Ctrl+C during a blocking read. A real cancel
        # keystroke is tracked as F10 in docs/FEATURE_REQUESTS.md.
        pass
    finally:
        keyboard.close()
        app.close()
    return 0


def _print_check_report(app: Application, args: argparse.Namespace) -> int:
    """Write a diagnostic summary and run the F42 self-test.

    Returns the exit code: ``0`` when every step of ``run_self_check``
    passes, ``1`` when at least one step fails (or, in the fall-back
    case where the user passed ``--live`` but the host had no live
    backend, when ``MemorySink`` ends up active despite the request).
    """
    tts_name = type(app.sound_engine.tts).__name__
    lines = [
        f"asat {__version__}",
        f"platform       {sys.platform}",
        f"stdin tty      {sys.stdin.isatty()}",
        f"sink           {type(app.sink).__name__}",
        f"tts            {tts_name}",
        f"runner         {type(app.runner).__name__}",
        f"bank path      {args.bank if args.bank is not None else '(built-in default)'}",
        f"session path   {args.session if args.session is not None else '(fresh)'}",
        f"session id     {app.session.session_id}",
        f"bindings       {sum(len(m) for m in app.router.bindings.values())}",
    ]
    for line in lines:
        print(line)
    print()
    return run_self_check(
        app.sound_engine.bank,
        app.sink,
        bus=app.bus,
        stdout=sys.stdout,
        live_requested=bool(args.live),
    )


def _run(app: Application, keyboard: KeyboardReader) -> None:
    """Read keys until the Application flags itself as done."""
    while app.running:
        key = keyboard.read_key()
        if key is None:
            break
        app.handle_key(key)
        for cell_id in app.drain_pending():
            app.execute(cell_id)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Define the CLI surface; kept tiny on purpose."""
    parser = argparse.ArgumentParser(
        prog="asat",
        description="Launch the Accessible Spatial Audio Terminal.",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        default=None,
        help=(
            "Workspace directory to open, OR a path to a `.asatnb` "
            "notebook file (the enclosing workspace is inferred). "
            "Omit for the legacy single-file mode driven by --session."
        ),
    )
    parser.add_argument(
        "notebook",
        nargs="?",
        default=None,
        help=(
            "Notebook name (stem) within the workspace to open. "
            "Defaults to the last opened notebook, or `default.asatnb` "
            "when the workspace is empty. Ignored when `workspace` is "
            "already a `.asatnb` file."
        ),
    )
    parser.add_argument(
        "--init-workspace",
        action="store_true",
        help=(
            "Initialise a fresh workspace at `workspace` (creates "
            "`<root>/.asat/config.json` and `<root>/notebooks/`) and "
            "then open it. Refuses to clobber an existing workspace."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Play audio on the platform live-speaker sink. Windows "
            "uses winsound; Linux/macOS use whichever of paplay, aplay, "
            "or afplay is installed. On a TTY this is the default — "
            "pass --no-live to override."
        ),
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help=(
            "Opt out of the auto-live default when stdout is a TTY. "
            "The session falls back to MemorySink (or --wav-dir DIR "
            "if provided) so audio never reaches the speakers."
        ),
    )
    parser.add_argument(
        "--tts",
        default=None,
        help=(
            "Pick the TTS engine by id (pyttsx3, espeak-ng, say, tone). "
            "Omit to auto-select the first available engine in priority "
            "order. Tests and scripts that need deterministic output "
            "pass `--tts tone`. Respect ASAT_TTS_ENGINE env var as the "
            "user-level default when this flag is absent."
        ),
    )
    parser.add_argument(
        "--wav-dir",
        type=Path,
        default=None,
        help=(
            "Also write each rendered buffer to a numbered WAV file in "
            "this directory. Useful for off-device review or when "
            "--live is not available."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the text trace on stdout. Audio output is unaffected.",
    )
    parser.add_argument(
        "--view",
        choices=("trace", "outline", "both"),
        default=None,
        help=(
            "Pick which text pane(s) to draw on stdout. `trace` is the "
            "original line-by-line event log; `outline` is an indented "
            "tree of cells with a `>` arrow marking the focused one; "
            "`both` stacks the outline under the trace. Defaults to "
            "`both` when stdout is a TTY and `trace` otherwise. Ignored "
            "under --quiet / --check."
        ),
    )
    parser.add_argument(
        "--bank",
        type=Path,
        default=None,
        help="Load the SoundBank from this JSON file instead of the built-in default.",
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=None,
        help="Load an existing Session from this JSON file; saved on exit.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the asat version string and exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Build the Application, print a diagnostic summary "
            "(sink, bank, session, TTY state), and exit without "
            "entering the key-read loop. Useful for smoke-testing "
            "a fresh install."
        ),
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help=(
            "Write every event on the bus to this JSON-lines file. "
            "The file is truncated at session start so a long-running "
            "install does not grow an unbounded log. Useful for "
            "diagnosing an audio issue: attach the file to an issue "
            "and a maintainer can replay it locally."
        ),
    )
    parser.add_argument(
        "--no-shared-shell",
        action="store_true",
        help=(
            "Disable the persistent shell backend (F60). Each cell "
            "spawns its own one-shot subprocess as in pre-0.8 ASAT. "
            "Use this when state-leakage between cells is undesirable, "
            "or when bash is missing and the fallback would be silent."
        ),
    )
    return parser.parse_args(argv)


class _FriendlyExit(Exception):
    """Raised when the CLI should abort with a one-line stderr message."""


def _load_bank(path: Optional[Path]) -> Optional[SoundBank]:
    """Load a SoundBank from disk, or return None for the built-in default.

    A missing file aborts with a friendly message rather than a raw
    FileNotFoundError traceback, because the most common cause is a
    typo and the user deserves to see it before anything else happens.
    """
    if path is None:
        return None
    if not path.exists():
        raise _FriendlyExit(
            f"--bank {path}: file not found. Check the path or omit "
            "the flag to use the built-in default bank."
        )
    return SoundBank.load(path)


def _asat_home() -> Path:
    """Return the per-user directory ASAT stores state in.

    Defaults to `~/.asat`. The `ASAT_HOME` environment variable
    overrides the default — useful for portable installs, for running
    two ASAT copies side by side, and for keeping the test suite from
    writing into a developer's real home directory.
    """
    override = os.environ.get("ASAT_HOME")
    if override:
        return Path(override)
    return Path.home() / ".asat"


def _onboarding_factory(
    *, quiet: bool, check: bool, has_live_audio: bool
):
    """Return an onboarding factory or None.

    `--quiet` opts out of the tour (the user has explicitly asked for
    a silent run). `--check` also skips it so the diagnostic report
    stays pure. Otherwise we hand `Application.build` a factory that
    builds an `OnboardingCoordinator` pointing at
    `<ASAT_HOME>/first-run-done` (default: `~/.asat/first-run-done`).

    `has_live_audio` is plumbed through so the coordinator can warn
    on stderr before the welcome narration vanishes into a silent sink
    (F41). True when the user passed `--live` or `--wav-dir DIR`.
    """
    if quiet or check:
        return None
    sentinel = _asat_home() / "first-run-done"

    def _factory(bus) -> OnboardingCoordinator:
        return OnboardingCoordinator(
            bus, sentinel, has_live_audio=has_live_audio
        )

    return _factory


def _log_factory(path: Optional[Path]):
    """Return a factory that attaches a `JsonlEventLogger`, or None.

    `--log PATH` opens `PATH` for writing at session start; no flag
    means no logger attaches. The factory shape matches
    `clipboard_factory` and `onboarding_factory` — it receives the
    freshly-built bus so the logger can subscribe before the first
    startup event fires.
    """
    if path is None:
        return None

    def _factory(bus) -> JsonlEventLogger:
        return JsonlEventLogger(bus, path)

    return _factory


def _pick_runner(*, no_shared_shell: bool, quiet: bool):
    """Return the execution runner the kernel should use.

    Defaults to a `ShellBackend` (F60) so cells share state. Falls back
    to `ProcessRunner` (the per-cell-Popen model) when bash is missing,
    on Windows, or when `--no-shared-shell` was requested. A one-line
    stderr note explains the fallback so the user is never surprised
    by silently per-cell behaviour.
    """
    if no_shared_shell:
        return ProcessRunner()
    backend = shell_backend_or_none()
    if backend is not None:
        return backend
    if not quiet:
        print(
            "[asat] persistent shell backend unavailable on this host; "
            "falling back to per-cell subprocesses. State will not "
            "carry between cells. Pass --no-shared-shell to silence.",
            file=sys.stderr,
        )
    return ProcessRunner()


def _resolve_workspace(
    args: argparse.Namespace,
) -> tuple[Optional[Workspace], Optional[Path]]:
    """Map CLI args onto a (workspace, session_path) pair.

    Three shapes are supported:

      1. ``asat`` (no positional, no flag) → legacy mode. Returns
         ``(None, None)`` so the caller honours ``--session``.
      2. ``asat <dir>`` → load the workspace at ``<dir>`` and pick
         the default notebook (last opened, sole existing, or a
         freshly-created ``default.asatnb``).
      3. ``asat <dir> <name>`` → load the workspace and resolve the
         notebook name relative to its notebooks dir.
      4. ``asat <file.asatnb>`` → walk up to find the enclosing
         workspace and open that notebook directly. The user does
         not have to know the workspace root.
      5. ``asat --init-workspace <dir>`` → ``Workspace.init`` then
         the same default-notebook resolution as case 2.
    """
    raw = args.workspace
    init = args.init_workspace
    notebook = args.notebook
    if raw is None:
        if init:
            raise _FriendlyExit(
                "--init-workspace requires a directory path argument."
            )
        return None, None
    target = Path(raw)
    if init:
        if notebook is not None:
            raise _FriendlyExit(
                "--init-workspace does not accept a notebook name; "
                "create the workspace first, then open notebooks by name."
            )
        if Workspace.is_workspace(target):
            raise _FriendlyExit(
                f"--init-workspace: {target} is already a workspace. "
                "Drop the flag to open it."
            )
        try:
            workspace = Workspace.init(target)
        except WorkspaceError as exc:
            raise _FriendlyExit(str(exc)) from exc
        return workspace, workspace.default_notebook()
    if target.suffix == WORKSPACE_NOTEBOOK_EXTENSION:
        if not target.exists():
            raise _FriendlyExit(f"notebook not found: {target}")
        if notebook is not None:
            raise _FriendlyExit(
                "cannot pass both a `.asatnb` file and a notebook name."
            )
        workspace = Workspace.find_enclosing(target)
        if workspace is None:
            raise _FriendlyExit(
                f"{target} is not inside an ASAT workspace. Run "
                "`asat --init-workspace <dir>` to create one."
            )
        return workspace, target.resolve()
    if not target.exists():
        raise _FriendlyExit(
            f"workspace not found: {target}. Pass --init-workspace "
            "to create it."
        )
    if not Workspace.is_workspace(target):
        raise _FriendlyExit(
            f"{target} is not an ASAT workspace. Pass "
            "--init-workspace to initialise it."
        )
    workspace = Workspace.load(target)
    if notebook is not None:
        try:
            session_path = workspace.notebook_path(notebook)
        except WorkspaceError as exc:
            raise _FriendlyExit(str(exc)) from exc
        if not session_path.exists():
            raise _FriendlyExit(
                f"notebook {notebook!r} not found in {workspace.root}. "
                "Use `:new-notebook <name>` (or `asat <dir> --init-workspace`) "
                "to create it."
            )
        return workspace, session_path
    return workspace, workspace.default_notebook()


def _load_session(path: Optional[Path]) -> Optional[Session]:
    """Load a Session from disk, or return None for a fresh session.

    A missing `--session` path is NOT an error: the semantics are
    "resume from this file if it exists, else create a fresh session
    and save to this path on exit". This matches the natural
    expectation of `python -m asat --session work.json` on a blank
    workspace and keeps first-run onboarding from crashing.
    """
    if path is None or not path.exists():
        return None
    return Session.load(path)


def _resolve_tts(
    registry: TTSEngineRegistry, engine_id: Optional[str]
) -> TTSEngine:
    """Build a TTS engine: explicit id if given, else registry default.

    Explicit flags always win over auto-selection so scripts and
    tests can pin a deterministic engine. A missing engine id raises
    ``TTSRegistryError`` which the caller surfaces as a friendly exit.
    """
    if engine_id is None:
        return registry.select_default()
    return registry.build(engine_id)


def _resolve_view_mode(args: argparse.Namespace) -> str:
    """Pick which text pane(s) to attach to stdout.

    Explicit ``--view`` wins. Without the flag, a real TTY defaults to
    ``"both"`` (the outline reads well when rendered alongside the
    trace); piped or captured stdout drops back to ``"trace"`` because
    the outline's repeated re-renders would be noise in a log file.
    ``--quiet`` and ``--check`` disable the renderer entirely upstream,
    so the return value does not matter in those modes; we still
    answer ``"trace"`` to keep the downstream branch predictable.
    """
    if args.view is not None:
        return args.view
    if args.quiet or args.check:
        return "trace"
    try:
        is_tty = bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        is_tty = False
    return "both" if is_tty else "trace"


def _resolve_live_preference(args: argparse.Namespace) -> bool:
    """Decide whether the sink-builder should try the live backend.

    Explicit wins over implicit: ``--no-live`` always returns False,
    ``--live`` always returns True. Otherwise auto-live engages when
    we are attached to a real TTY and the user hasn't asked for
    ``--quiet`` or ``--check``. Piped / captured runs keep the safe
    MemorySink default, which is what the test suite relies on.
    """
    if args.no_live:
        return False
    if args.live:
        return True
    if args.quiet or args.check or args.wav_dir is not None:
        return False
    # Gate on stdout.isatty so pytest runs with mocked StringIO stdout
    # stay deterministic. An interactive shell passes this check and
    # gets live audio out of the box.
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _make_sink(
    wav_dir: Optional[Path],
    live: bool,
    *,
    quiet: bool = False,
) -> AudioSink:
    """Compose the sink chain based on the requested flags.

    Priority: `--live` drives the primary sink when available.
    `--wav-dir DIR` can be layered on either the live sink or the
    default MemorySink. When `--live` is requested on a platform
    without a live backend, we print a short explanation and continue
    with MemorySink so the session still starts.
    """
    primary: AudioSink
    if live:
        try:
            primary = pick_live_sink()
        except LiveAudioUnavailable as exc:
            if not quiet:
                print(f"[asat] --live unavailable: {exc}", file=sys.stderr)
                if wav_dir is None:
                    print(
                        "[asat] falling back to the in-memory sink. "
                        "Pair with --wav-dir DIR to capture audio as WAVs "
                        "you can play back (tracked as F6 in "
                        "docs/FEATURE_REQUESTS.md).",
                        file=sys.stderr,
                    )
            primary = MemorySink()
    else:
        primary = MemorySink()
    if wav_dir is None:
        return primary
    wav_dir.mkdir(parents=True, exist_ok=True)
    wav_sink = WavFileSink(wav_dir)
    return _TeeSink(primary, wav_sink)


class _TeeSink:
    """Dispatch each buffer to two underlying sinks.

    Private to the CLI because no other layer needs it today. If
    multi-sink composition grows into a real feature, promote this to
    `asat/audio_sink.py`.
    """

    def __init__(self, first: AudioSink, second: AudioSink) -> None:
        self._first = first
        self._second = second

    def play(self, buffer) -> None:
        self._first.play(buffer)
        self._second.play(buffer)

    def close(self) -> None:
        try:
            self._first.close()
        finally:
            self._second.close()


if __name__ == "__main__":
    sys.exit(main())
