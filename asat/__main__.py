"""Launch the Accessible Spatial Audio Terminal from the command line.

`python -m asat` assembles an Application with sensible defaults and
drives it from real keystrokes. By default the session narrates to an
in-memory sink (so every platform starts cleanly) and prints a small
text trace to stdout (so sighted viewers and anyone debugging can
follow along). The flags below switch the two knobs:

    --live          play audio through the platform live-speaker sink
                    (Windows today; POSIX falls back to MemorySink
                    with an explanation).
    --wav-dir DIR   additionally write every rendered buffer to a
                    numbered WAV file under DIR.
    --quiet         suppress the text trace on stdout (audio only).

Exit with the `:quit` meta-command (type `:quit` in INPUT mode then
press Enter) or by sending EOF (Ctrl+D on POSIX, Ctrl+Z then Enter on
Windows). The full keystroke cheat sheet lives in
docs/USER_MANUAL.md.
"""

from __future__ import annotations

import argparse
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
from asat.keyboard import KeyboardNotAvailable, KeyboardReader, pick_default
from asat.onboarding import OnboardingCoordinator
from asat.session import Session
from asat.sound_bank import SoundBank


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse args, build the Application, and drive the read-dispatch loop."""
    args = _parse_args(argv)
    if args.version:
        print(f"asat {__version__}")
        return 0
    sink = _make_sink(args.wav_dir, args.live, quiet=args.quiet)
    if (
        not args.quiet
        and not args.check
        and not args.live
        and args.wav_dir is None
    ):
        # Tell first-time users why they are hearing silence without
        # forcing them to read the docs. Suppressed once any audio
        # destination is requested explicitly, and when --check is
        # printing its own diagnostic report.
        print(
            "[asat] audio is going to the in-memory sink. Pass --live "
            "(Windows) or --wav-dir DIR to hear or capture it.",
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
    onboarding_factory = _onboarding_factory(quiet=args.quiet, check=args.check)
    app = Application.build(
        sink=sink,
        bank=bank,
        bank_path=args.bank,
        session=session,
        session_path=args.session,
        text_trace=trace_stream,
        clipboard_factory=SystemClipboard,
        onboarding_factory=onboarding_factory,
    )
    if args.check:
        _print_check_report(app, args)
        app.close()
        return 0
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


def _print_check_report(app: Application, args: argparse.Namespace) -> None:
    """Write a diagnostic summary and return without starting the loop."""
    lines = [
        f"asat {__version__}",
        f"platform       {sys.platform}",
        f"stdin tty      {sys.stdin.isatty()}",
        f"sink           {type(app.sink).__name__}",
        f"bank path      {args.bank if args.bank is not None else '(built-in default)'}",
        f"session path   {args.session if args.session is not None else '(fresh)'}",
        f"session id     {app.session.session_id}",
        f"bindings       {sum(len(m) for m in app.router.bindings.values())}",
    ]
    for line in lines:
        print(line)


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
        "--live",
        action="store_true",
        help=(
            "Play audio on the platform live-speaker sink. Today this "
            "means winsound on Windows; POSIX hosts fall back to the "
            "in-memory sink with an explanation."
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


def _onboarding_factory(
    *, quiet: bool, check: bool
):
    """Return an onboarding factory or None.

    `--quiet` opts out of the tour (the user has explicitly asked for
    a silent run). `--check` also skips it so the diagnostic report
    stays pure. Otherwise we hand `Application.build` a factory that
    builds an `OnboardingCoordinator` pointing at `~/.asat/first-run-done`.
    """
    if quiet or check:
        return None
    sentinel = Path.home() / ".asat" / "first-run-done"

    def _factory(bus) -> OnboardingCoordinator:
        return OnboardingCoordinator(bus, sentinel)

    return _factory


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
