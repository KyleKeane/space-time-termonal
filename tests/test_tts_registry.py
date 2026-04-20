"""Unit tests for the pluggable TTS engine registry (F28, PR 1).

The registry is the user-facing surface for picking a TTS backend. The
tests here pin down four behaviours:

* ``available_ids()`` reflects live ``available()`` probes so tests can
  predict exactly which engines show up on a given host.
* The default priority order walks from the most-featureful backend
  (``pyttsx3``) down to the deterministic ``tone`` fallback.
* ``ASAT_TTS_ENGINE`` pins the selected engine without touching code.
* ``build()`` forwards parameters to the adapter factory so
  ``:tts set`` can re-build a live engine with one knob changed.

The registry never actually talks to the backends during these tests;
the heavy-weight synthesizers are covered elsewhere (or not at all —
``pyttsx3`` / ``espeak-ng`` are integration-only).
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from asat.tts import ToneTTSEngine
from asat.tts_registry import (
    DEFAULT_PRIORITY,
    TTSEngineRegistry,
    TTSEngineSpec,
    TTSParameter,
    TTSRegistryError,
)


class RegistryDefaultsTests(unittest.TestCase):

    def test_default_registry_exposes_all_builtin_ids(self) -> None:
        registry = TTSEngineRegistry.default()
        ids = {spec.id for spec in registry.specs}
        self.assertEqual(ids, {"pyttsx3", "espeak-ng", "say", "tone"})

    def test_default_priority_order_matches_plan(self) -> None:
        registry = TTSEngineRegistry.default()
        self.assertEqual(
            registry.priority,
            ("pyttsx3", "espeak-ng", "say", "tone"),
        )
        self.assertEqual(DEFAULT_PRIORITY, registry.priority)

    def test_tone_is_always_available(self) -> None:
        registry = TTSEngineRegistry.default()
        # The tone engine has no external dependencies; it must always
        # be reachable even in headless CI with no speech libs.
        self.assertIn("tone", registry.available_ids())


class ResolveDefaultIdTests(unittest.TestCase):

    def _registry_with_availability(
        self, **availability: bool
    ) -> TTSEngineRegistry:
        """Build a registry whose specs report fixed availability."""
        specs = []
        priority = []
        for engine_id, available in availability.items():
            specs.append(
                TTSEngineSpec(
                    id=engine_id,
                    describe=f"{engine_id} test spec",
                    factory=lambda sr, params, _id=engine_id: ToneTTSEngine(
                        sample_rate=sr
                    ),
                    available=lambda _flag=available: _flag,
                )
            )
            priority.append(engine_id)
        return TTSEngineRegistry(specs=tuple(specs), priority=tuple(priority))

    def test_picks_first_available_in_priority_order(self) -> None:
        registry = self._registry_with_availability(
            pyttsx3=False, espeak=True, tone=True
        )
        self.assertEqual(registry.resolve_default_id(), "espeak")

    def test_skips_unavailable_engines(self) -> None:
        registry = self._registry_with_availability(
            pyttsx3=False, espeak=False, tone=True
        )
        self.assertEqual(registry.resolve_default_id(), "tone")

    def test_env_var_override_pins_the_engine(self) -> None:
        registry = self._registry_with_availability(
            pyttsx3=True, espeak=True, tone=True
        )
        with mock.patch.dict(os.environ, {"ASAT_TTS_ENGINE": "tone"}):
            self.assertEqual(registry.resolve_default_id(), "tone")

    def test_env_var_override_is_ignored_when_unknown(self) -> None:
        registry = self._registry_with_availability(
            pyttsx3=True, espeak=True, tone=True
        )
        with mock.patch.dict(os.environ, {"ASAT_TTS_ENGINE": "bogus"}):
            # Unknown override falls back to priority resolution so a
            # typo in the env does not brick the session.
            self.assertEqual(registry.resolve_default_id(), "pyttsx3")

    def test_available_ids_filters_unavailable(self) -> None:
        registry = self._registry_with_availability(
            pyttsx3=False, espeak=True, tone=True
        )
        self.assertEqual(registry.available_ids(), ("espeak", "tone"))


class BuildTests(unittest.TestCase):

    def test_build_tone_returns_tone_engine(self) -> None:
        registry = TTSEngineRegistry.default()
        engine = registry.build("tone", sample_rate=22050)
        self.assertIsInstance(engine, ToneTTSEngine)
        self.assertEqual(engine.sample_rate, 22050)

    def test_build_unknown_id_raises(self) -> None:
        registry = TTSEngineRegistry.default()
        with self.assertRaises(TTSRegistryError):
            registry.build("does-not-exist")

    def test_build_forwards_parameters_to_factory(self) -> None:
        captured: dict[str, object] = {}

        def factory(sample_rate: int, params: dict[str, object]) -> ToneTTSEngine:
            captured["sample_rate"] = sample_rate
            captured["params"] = params
            return ToneTTSEngine(sample_rate=sample_rate)

        spec = TTSEngineSpec(
            id="probe",
            describe="probe spec",
            factory=factory,
            available=lambda: True,
            parameters=(TTSParameter("rate", "words per minute"),),
        )
        registry = TTSEngineRegistry(specs=(spec,), priority=("probe",))
        registry.build("probe", sample_rate=16000, parameters={"rate": 180})
        self.assertEqual(captured["sample_rate"], 16000)
        self.assertEqual(captured["params"], {"rate": 180})

    def test_select_default_honours_env_var(self) -> None:
        registry = TTSEngineRegistry.default()
        with mock.patch.dict(os.environ, {"ASAT_TTS_ENGINE": "tone"}):
            engine = registry.select_default(sample_rate=8000)
        self.assertIsInstance(engine, ToneTTSEngine)
        self.assertEqual(engine.sample_rate, 8000)

    def test_select_default_walks_priority_when_no_env(self) -> None:
        # pyttsx3 and espeak-ng likely aren't installed in CI, so the
        # tone fallback is the expected pick; either way the call must
        # succeed because `tone` always reports available=True.
        registry = TTSEngineRegistry.default()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASAT_TTS_ENGINE", None)
            engine = registry.select_default()
        # At minimum we got *some* TTS engine back.
        self.assertTrue(hasattr(engine, "synthesize"))
        self.assertTrue(hasattr(engine, "sample_rate"))


class DescribeTests(unittest.TestCase):

    def test_describe_all_lists_every_engine(self) -> None:
        registry = TTSEngineRegistry.default()
        text = registry.describe()
        self.assertIn("default:", text)
        for engine_id in ("pyttsx3", "espeak-ng", "say", "tone"):
            self.assertIn(engine_id, text)

    def test_describe_one_engine_includes_availability_marker(self) -> None:
        registry = TTSEngineRegistry.default()
        line = registry.describe("tone")
        self.assertIn("available", line)
        self.assertIn("tone", line)


class SpecForTests(unittest.TestCase):

    def test_spec_for_returns_registered_spec(self) -> None:
        registry = TTSEngineRegistry.default()
        spec = registry.spec_for("tone")
        self.assertEqual(spec.id, "tone")

    def test_spec_for_unknown_raises(self) -> None:
        registry = TTSEngineRegistry.default()
        with self.assertRaises(TTSRegistryError):
            registry.spec_for("nope")

    def test_has_reports_membership(self) -> None:
        registry = TTSEngineRegistry.default()
        self.assertTrue(registry.has("tone"))
        self.assertFalse(registry.has("nope"))


if __name__ == "__main__":
    unittest.main()
