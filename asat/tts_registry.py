"""Pluggable TTS engine registry.

The audio pipeline treats every TTS backend as an anonymous
`TTSEngine` protocol implementer; the registry is what actually lets
the user pick between them at runtime. Each engine ships with a stable
``id`` (e.g. ``"pyttsx3"``, ``"espeak-ng"``, ``"say"``, ``"tone"``), a
``describe`` string, and a ``Spec`` that carries:

- a ``factory`` that builds a live ``TTSEngine`` for the given sample
  rate and parameter dict,
- an ``available()`` callable that reports whether the backend can run
  on this host (binary installed, Python dep importable, etc.), and
- a ``parameters`` tuple describing the tunable knobs so the
  ``:tts set`` meta-command can list them and the settings editor can
  expose them.

The default priority order when nothing is configured is the one that
matches the user's own directive: "whichever works consistently across
Windows and POSIX, with the most customisation". That means
``Pyttsx3Engine`` first (one Python dep, routes to SAPI5 / espeak /
NSSpeechSynthesizer automatically), ``EspeakNgEngine`` second (Linux
native), ``SystemSayEngine`` third (macOS native), and finally the
deterministic ``ToneTTSEngine`` fallback that always works — even in
headless CI with no audio libraries installed.

Calling ``select_default()`` walks the priority list top-to-bottom and
returns the first engine whose ``available()`` says yes. The function
never raises — ``ToneTTSEngine`` is the last-resort floor.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from asat.audio import DEFAULT_SAMPLE_RATE
from asat.tts import (
    EspeakNgEngine,
    Pyttsx3Engine,
    SystemSayEngine,
    ToneTTSEngine,
    TTSEngine,
)


@dataclass(frozen=True)
class TTSParameter:
    """One tunable knob on a TTS engine adapter.

    ``name`` is the key the user types after ``:tts set`` (e.g.
    ``rate``, ``voice``, ``pitch``, ``volume``). ``description`` is a
    short blurb read aloud by ``:tts list`` / ``:tts set`` so the user
    knows what the knob does without opening the docs.
    """

    name: str
    description: str


@dataclass(frozen=True)
class TTSEngineSpec:
    """Registry entry describing how to build and probe one engine."""

    id: str
    describe: str
    factory: Callable[[int, dict[str, Any]], TTSEngine]
    available: Callable[[], bool]
    parameters: tuple[TTSParameter, ...] = field(default_factory=tuple)


def _always_available() -> bool:
    """Sentinel for engines that never fail to construct (the tone fallback)."""
    return True


DEFAULT_PRIORITY: tuple[str, ...] = (
    "pyttsx3",
    "espeak-ng",
    "say",
    "tone",
)


def _build_pyttsx3(sample_rate: int, params: dict[str, Any]) -> TTSEngine:
    return Pyttsx3Engine(sample_rate=sample_rate, **_coerce(params))


def _build_espeak_ng(sample_rate: int, params: dict[str, Any]) -> TTSEngine:
    return EspeakNgEngine(sample_rate=sample_rate, **_coerce(params))


def _build_system_say(sample_rate: int, params: dict[str, Any]) -> TTSEngine:
    return SystemSayEngine(sample_rate=sample_rate, **_coerce(params))


def _build_tone(sample_rate: int, params: dict[str, Any]) -> TTSEngine:
    _ = params  # tone engine ignores every parameter by design.
    return ToneTTSEngine(sample_rate=sample_rate)


def _coerce(params: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so adapters rely on their defaults."""
    return {k: v for k, v in params.items() if v is not None}


_BUILTIN_SPECS: tuple[TTSEngineSpec, ...] = (
    TTSEngineSpec(
        id="pyttsx3",
        describe=(
            "pyttsx3 — cross-platform (Windows SAPI, macOS NSSpeechSynthesizer, "
            "Linux espeak). Adjustable voice / rate / volume / pitch."
        ),
        factory=_build_pyttsx3,
        available=Pyttsx3Engine.available,
        parameters=(
            TTSParameter("voice", "Backend voice id (platform-specific)."),
            TTSParameter("rate", "Words per minute (default ~200)."),
            TTSParameter("volume", "Linear gain in [0.0, 1.0]."),
            TTSParameter("pitch", "Pitch multiplier (some backends ignore)."),
        ),
    ),
    TTSEngineSpec(
        id="espeak-ng",
        describe=(
            "espeak-ng — small, fast, ubiquitous on Linux. Works through a "
            "subprocess pipe; highly configurable pitch / speed / amplitude."
        ),
        factory=_build_espeak_ng,
        available=EspeakNgEngine.available,
        parameters=(
            TTSParameter("voice", "espeak-ng -v voice code, e.g. en-us, en-gb."),
            TTSParameter("speed", "Words per minute (espeak -s, 80..500)."),
            TTSParameter("pitch", "Pitch 0..99 (espeak -p, default 50)."),
            TTSParameter("amplitude", "Amplitude 0..200 (espeak -a, default 100)."),
        ),
    ),
    TTSEngineSpec(
        id="say",
        describe=(
            "macOS say(1) — native high-quality voices. Requires Darwin. "
            "Adjustable voice name and rate."
        ),
        factory=_build_system_say,
        available=SystemSayEngine.available,
        parameters=(
            TTSParameter("voice", "say -v voice name, e.g. Samantha."),
            TTSParameter("rate", "Words per minute (say -r)."),
        ),
    ),
    TTSEngineSpec(
        id="tone",
        describe=(
            "Deterministic tone stand-in. Always available. Used by tests "
            "and as the last-resort fallback when no speech engine is "
            "installed."
        ),
        factory=_build_tone,
        available=_always_available,
        parameters=(),
    ),
)


class TTSRegistryError(ValueError):
    """Raised when the registry is asked for an engine it does not know."""


class TTSEngineRegistry:
    """Pluggable catalogue of TTS engine adapters.

    ``default()`` returns the registry preloaded with every in-tree
    adapter and the standard priority order. Custom callers can build a
    registry with a tailored list of specs and priority.
    """

    def __init__(
        self,
        specs: tuple[TTSEngineSpec, ...] = _BUILTIN_SPECS,
        priority: tuple[str, ...] = DEFAULT_PRIORITY,
    ) -> None:
        """Wrap a pre-built tuple of specs plus a priority order."""
        self._specs: dict[str, TTSEngineSpec] = {spec.id: spec for spec in specs}
        self._priority = priority

    @classmethod
    def default(cls) -> "TTSEngineRegistry":
        """Return a registry loaded with every in-tree adapter."""
        return cls()

    @property
    def specs(self) -> tuple[TTSEngineSpec, ...]:
        """Return every registered spec in declaration order."""
        return tuple(self._specs[engine_id] for engine_id in self._priority if engine_id in self._specs)

    @property
    def priority(self) -> tuple[str, ...]:
        """Return the id-order select_default() walks top-to-bottom."""
        return self._priority

    def has(self, engine_id: str) -> bool:
        """Return True when an engine with that id is registered."""
        return engine_id in self._specs

    def spec_for(self, engine_id: str) -> TTSEngineSpec:
        """Return the spec for an engine id or raise ``TTSRegistryError``."""
        try:
            return self._specs[engine_id]
        except KeyError as exc:
            raise TTSRegistryError(
                f"unknown TTS engine id: {engine_id!r}. Known ids: "
                f"{', '.join(sorted(self._specs))}"
            ) from exc

    def available_ids(self) -> tuple[str, ...]:
        """Return the ids of every engine whose backend is usable here."""
        return tuple(
            engine_id
            for engine_id in self._priority
            if engine_id in self._specs and self._specs[engine_id].available()
        )

    def select_default(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        *,
        parameters: Optional[dict[str, Any]] = None,
    ) -> TTSEngine:
        """Build the first available engine in priority order.

        Honours ``ASAT_TTS_ENGINE`` when set to a known id — the env
        var is the lowest-friction override for users and for test
        sandboxes that want to pin the backend without a config file.
        """
        override = os.environ.get("ASAT_TTS_ENGINE")
        if override:
            return self.build(override, sample_rate=sample_rate, parameters=parameters)
        chosen_id = self.resolve_default_id()
        return self.build(chosen_id, sample_rate=sample_rate, parameters=parameters)

    def resolve_default_id(self) -> str:
        """Return the engine id ``select_default`` would pick right now.

        Useful for diagnostics (``--check``, the ``:tts list`` readout)
        that want to name the engine without actually constructing one.
        """
        override = os.environ.get("ASAT_TTS_ENGINE")
        if override and self.has(override):
            return override
        for engine_id in self._priority:
            spec = self._specs.get(engine_id)
            if spec is not None and spec.available():
                return engine_id
        # DEFAULT_PRIORITY ends in "tone" which is always available, so
        # this fallthrough is only reachable with a pathological custom
        # registry that dropped the tone adapter.
        return next(iter(self._priority), "tone")

    def build(
        self,
        engine_id: str,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        parameters: Optional[dict[str, Any]] = None,
    ) -> TTSEngine:
        """Build an engine by id; raise ``TTSRegistryError`` when unknown."""
        spec = self.spec_for(engine_id)
        return spec.factory(sample_rate, dict(parameters or {}))

    def describe(self, engine_id: Optional[str] = None) -> str:
        """Return a one-line description of one engine or the full list.

        With ``engine_id`` None, returns a newline-joined block starting
        with the resolved default (so the reader hears what's live
        before the alternatives). With an id, returns only that engine's
        description prefixed with availability (``[available]`` /
        ``[not installed]``).
        """
        if engine_id is not None:
            spec = self.spec_for(engine_id)
            marker = "available" if spec.available() else "not installed"
            return f"[{marker}] {spec.id}: {spec.describe}"
        default_id = self.resolve_default_id()
        lines = [f"default: {default_id}"]
        for engine_id in self._priority:
            spec = self._specs.get(engine_id)
            if spec is None:
                continue
            marker = "available" if spec.available() else "not installed"
            lines.append(f"  [{marker}] {spec.id}: {spec.describe}")
        return "\n".join(lines)
