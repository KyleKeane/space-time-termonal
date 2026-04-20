"""Unit tests for the SoundBank data model."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from asat.events import EventType
from asat.sound_bank import (
    EventBinding,
    SCHEMA_VERSION,
    SOUND_KINDS,
    SoundBank,
    SoundBankError,
    SoundRecipe,
    Voice,
)


def _sample_bank() -> SoundBank:
    """Build a small valid bank reused across tests."""
    voice = Voice(id="narrator", rate=1.1, pitch=0.95, azimuth=-15.0)
    sound = SoundRecipe(
        id="ding",
        kind="tone",
        params={"frequency": 880.0, "duration": 0.15, "waveform": "sine"},
        volume=0.6,
    )
    binding = EventBinding(
        id="cell_created",
        event_type="cell.created",
        voice_id="narrator",
        sound_id="ding",
        say_template="new cell {cell_id}",
        priority=200,
    )
    return SoundBank(voices=(voice,), sounds=(sound,), bindings=(binding,))


class VoiceTests(unittest.TestCase):

    def test_voice_defaults_are_neutral(self) -> None:
        voice = Voice(id="v1")
        self.assertEqual(voice.engine, "")
        self.assertEqual(voice.rate, 1.0)
        self.assertEqual(voice.pitch, 1.0)
        self.assertEqual(voice.volume, 1.0)
        self.assertEqual(voice.azimuth, 0.0)
        self.assertEqual(voice.elevation, 0.0)

    def test_voice_round_trip_through_dict(self) -> None:
        original = Voice(id="v1", engine="sapi", rate=1.2, azimuth=30.0)
        restored = Voice.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_voice_rejects_missing_id(self) -> None:
        with self.assertRaises(SoundBankError):
            Voice.from_dict({"rate": 1.0})

    def test_voice_rejects_zero_rate(self) -> None:
        with self.assertRaises(SoundBankError):
            Voice.from_dict({"id": "v1", "rate": 0})

    def test_voice_rejects_out_of_range_azimuth(self) -> None:
        with self.assertRaises(SoundBankError):
            Voice.from_dict({"id": "v1", "azimuth": 200})


class SoundRecipeTests(unittest.TestCase):

    def test_all_documented_kinds_are_accepted(self) -> None:
        for kind in SOUND_KINDS:
            recipe = SoundRecipe.from_dict({"id": f"s_{kind}", "kind": kind})
            self.assertEqual(recipe.kind, kind)

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundRecipe.from_dict({"id": "s1", "kind": "banana"})

    def test_params_must_be_mapping(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundRecipe.from_dict({"id": "s1", "kind": "tone", "params": [1, 2]})

    def test_recipe_round_trip_preserves_params(self) -> None:
        recipe = SoundRecipe(
            id="chord1", kind="chord", params={"frequencies": [440.0, 660.0]}
        )
        restored = SoundRecipe.from_dict(recipe.to_dict())
        self.assertEqual(restored, recipe)


class EventBindingTests(unittest.TestCase):

    def test_binding_requires_some_effect(self) -> None:
        with self.assertRaises(SoundBankError):
            EventBinding.from_dict({"id": "b1", "event_type": "cell.created"})

    def test_binding_accepts_template_only(self) -> None:
        binding = EventBinding.from_dict(
            {
                "id": "b1",
                "event_type": "cell.created",
                "say_template": "hello",
            }
        )
        self.assertEqual(binding.say_template, "hello")
        self.assertIsNone(binding.voice_id)
        self.assertIsNone(binding.sound_id)

    def test_binding_null_voice_id_round_trips(self) -> None:
        binding = EventBinding.from_dict(
            {
                "id": "b1",
                "event_type": "cell.created",
                "voice_id": None,
                "sound_id": "ding",
            }
        )
        self.assertIsNone(binding.voice_id)
        self.assertEqual(binding.sound_id, "ding")

    def test_binding_priority_defaults_to_100(self) -> None:
        binding = EventBinding.from_dict(
            {"id": "b1", "event_type": "cell.created", "sound_id": "ding"}
        )
        self.assertEqual(binding.priority, 100)
        self.assertTrue(binding.enabled)
        self.assertEqual(binding.voice_overrides, {})
        self.assertEqual(binding.sound_overrides, {})

    def test_binding_accepts_voice_and_sound_overrides(self) -> None:
        binding = EventBinding.from_dict(
            {
                "id": "b1",
                "event_type": "cell.created",
                "voice_id": "narrator",
                "sound_id": "ding",
                "voice_overrides": {"pitch": 0.8, "azimuth": -30.0},
                "sound_overrides": {"volume": 0.4},
            }
        )
        self.assertEqual(binding.voice_overrides, {"pitch": 0.8, "azimuth": -30.0})
        self.assertEqual(binding.sound_overrides, {"volume": 0.4})

    def test_binding_rejects_unknown_voice_override_key(self) -> None:
        with self.assertRaises(SoundBankError):
            EventBinding.from_dict(
                {
                    "id": "b1",
                    "event_type": "cell.created",
                    "sound_id": "ding",
                    "voice_overrides": {"engine": "sapi"},
                }
            )

    def test_binding_rejects_unknown_sound_override_key(self) -> None:
        with self.assertRaises(SoundBankError):
            EventBinding.from_dict(
                {
                    "id": "b1",
                    "event_type": "cell.created",
                    "sound_id": "ding",
                    "sound_overrides": {"kind": "chord"},
                }
            )

    def test_binding_roundtrips_overrides_through_dict(self) -> None:
        binding = EventBinding(
            id="b1",
            event_type="cell.created",
            voice_id="narrator",
            voice_overrides={"pitch": 1.2},
            sound_overrides={},
        )
        restored = EventBinding.from_dict(binding.to_dict())
        self.assertEqual(restored, binding)


class SoundBankStructureTests(unittest.TestCase):

    def test_empty_bank_is_valid(self) -> None:
        bank = SoundBank()
        bank.validate()
        self.assertEqual(bank.voices, ())
        self.assertEqual(bank.version, SCHEMA_VERSION)

    def test_lookup_helpers_return_records(self) -> None:
        bank = _sample_bank()
        self.assertEqual(bank.voice_for("narrator").rate, 1.1)
        self.assertEqual(bank.sound_for("ding").kind, "tone")
        bindings = bank.bindings_for("cell.created")
        self.assertEqual(len(bindings), 1)

    def test_lookup_helpers_return_none_for_missing_ids(self) -> None:
        bank = _sample_bank()
        self.assertIsNone(bank.voice_for("ghost"))
        self.assertIsNone(bank.sound_for("ghost"))
        self.assertEqual(bank.bindings_for("unused.event"), ())

    def test_bindings_sorted_by_priority_desc(self) -> None:
        voice = Voice(id="v1")
        bindings = (
            EventBinding(id="b_low", event_type="cell.created", voice_id="v1", priority=10),
            EventBinding(id="b_hi", event_type="cell.created", voice_id="v1", priority=500),
            EventBinding(id="b_mid", event_type="cell.created", voice_id="v1", priority=100),
        )
        bank = SoundBank(voices=(voice,), bindings=bindings)
        ordered = bank.bindings_for("cell.created")
        self.assertEqual([b.id for b in ordered], ["b_hi", "b_mid", "b_low"])

    def test_disabled_bindings_hidden_by_default(self) -> None:
        voice = Voice(id="v1")
        bindings = (
            EventBinding(id="on", event_type="cell.created", voice_id="v1"),
            EventBinding(id="off", event_type="cell.created", voice_id="v1", enabled=False),
        )
        bank = SoundBank(voices=(voice,), bindings=bindings)
        self.assertEqual([b.id for b in bank.bindings_for("cell.created")], ["on"])
        full = bank.bindings_for("cell.created", include_disabled=True)
        self.assertEqual({b.id for b in full}, {"on", "off"})


class SoundBankValidationTests(unittest.TestCase):

    def test_duplicate_voice_ids_rejected(self) -> None:
        bank = SoundBank(voices=(Voice(id="v"), Voice(id="v")))
        with self.assertRaises(SoundBankError):
            bank.validate()

    def test_binding_pointing_at_missing_voice_rejected(self) -> None:
        bank = SoundBank(
            bindings=(
                EventBinding(id="b", event_type="cell.created", voice_id="ghost"),
            )
        )
        with self.assertRaises(SoundBankError):
            bank.validate()

    def test_binding_pointing_at_missing_sound_rejected(self) -> None:
        bank = SoundBank(
            bindings=(
                EventBinding(id="b", event_type="cell.created", sound_id="ghost"),
            )
        )
        with self.assertRaises(SoundBankError):
            bank.validate()


class SoundBankSerializationTests(unittest.TestCase):

    def test_dict_round_trip_is_equal(self) -> None:
        bank = _sample_bank()
        restored = SoundBank.from_dict(bank.to_dict())
        self.assertEqual(restored, bank)

    def test_load_from_path_returns_equal_bank(self) -> None:
        bank = _sample_bank()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bank.json"
            bank.save(path)
            restored = SoundBank.load(path)
        self.assertEqual(restored, bank)

    def test_save_creates_parent_directories(self) -> None:
        bank = _sample_bank()
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b" / "bank.json"
            bank.save(nested)
            self.assertTrue(nested.exists())

    def test_loader_rejects_wrong_version(self) -> None:
        bank = _sample_bank().to_dict()
        bank["version"] = 999
        with self.assertRaises(SoundBankError):
            SoundBank.from_dict(bank)

    def test_loader_rejects_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not valid json")
            with self.assertRaises(SoundBankError):
                SoundBank.load(path)

    def test_with_replaced_swaps_lists(self) -> None:
        bank = _sample_bank()
        new_voice = Voice(id="alt")
        updated = bank.with_replaced(voices=(new_voice,))
        self.assertEqual(updated.voices, (new_voice,))
        self.assertEqual(updated.sounds, bank.sounds)
        self.assertEqual(updated.bindings, bank.bindings)


class JsonSchemaFileTests(unittest.TestCase):

    def test_schema_file_is_valid_json(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "asat" / "sound_bank_schema.json"
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(data["properties"]["version"]["const"], SCHEMA_VERSION)
        kinds = data["$defs"]["sound"]["properties"]["kind"]["enum"]
        self.assertEqual(tuple(kinds), SOUND_KINDS)

    def test_schema_declares_ducking_fields(self) -> None:
        """F32: schema must list ducking_enabled and duck_level so the
        on-disk format documents them at the same level as the dataclass."""
        schema_path = Path(__file__).resolve().parents[1] / "asat" / "sound_bank_schema.json"
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(data["properties"]["ducking_enabled"]["type"], "boolean")
        self.assertEqual(data["properties"]["ducking_enabled"]["default"], True)
        duck_level = data["properties"]["duck_level"]
        self.assertEqual(duck_level["type"], "number")
        self.assertEqual(duck_level["minimum"], 0.0)
        self.assertEqual(duck_level["maximum"], 1.0)
        self.assertEqual(duck_level["default"], 0.4)


class DuckingFieldTests(unittest.TestCase):
    """F32 — ducking_enabled / duck_level live on the bank itself."""

    def test_defaults_match_spec(self) -> None:
        bank = SoundBank()
        self.assertTrue(bank.ducking_enabled)
        self.assertEqual(bank.duck_level, 0.4)

    def test_round_trip_preserves_non_default_values(self) -> None:
        bank = SoundBank(ducking_enabled=False, duck_level=0.2)
        restored = SoundBank.from_dict(bank.to_dict())
        self.assertFalse(restored.ducking_enabled)
        self.assertEqual(restored.duck_level, 0.2)

    def test_loader_supplies_defaults_when_fields_missing(self) -> None:
        """Older bank files won't carry the F32 fields. Loader fills them
        with the dataclass defaults so existing on-disk banks keep loading."""
        legacy = {"version": SCHEMA_VERSION}
        bank = SoundBank.from_dict(legacy)
        self.assertTrue(bank.ducking_enabled)
        self.assertEqual(bank.duck_level, 0.4)

    def test_duck_level_out_of_range_raises(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundBank.from_dict({"version": SCHEMA_VERSION, "duck_level": 1.5})
        with self.assertRaises(SoundBankError):
            SoundBank.from_dict({"version": SCHEMA_VERSION, "duck_level": -0.1})

    def test_duck_level_non_numeric_raises(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundBank.from_dict({"version": SCHEMA_VERSION, "duck_level": "loud"})


class VerbosityFieldTests(unittest.TestCase):
    """F31 — verbosity tier on bindings + bank-wide ceiling."""

    def _bank_with_tiered_bindings(self, bank_level: str) -> SoundBank:
        voice = Voice(id="v")
        return SoundBank(
            voices=(voice,),
            bindings=(
                EventBinding(
                    id="critical",
                    event_type=EventType.COMMAND_FAILED.value,
                    voice_id="v",
                    say_template="boom",
                    verbosity="minimal",
                ),
                EventBinding(
                    id="chatty",
                    event_type=EventType.COMMAND_FAILED.value,
                    voice_id="v",
                    say_template="details",
                    verbosity="verbose",
                ),
            ),
            verbosity_level=bank_level,
        )

    def test_defaults_match_spec(self) -> None:
        bank = SoundBank()
        self.assertEqual(bank.verbosity_level, "normal")
        self.assertEqual(
            EventBinding(id="b", event_type="x", say_template="t").verbosity,
            "normal",
        )

    def test_round_trip_preserves_non_default_values(self) -> None:
        bank = SoundBank(
            voices=(Voice(id="v"),),
            bindings=(
                EventBinding(
                    id="b",
                    event_type=EventType.COMMAND_FAILED.value,
                    voice_id="v",
                    say_template="hi",
                    verbosity="verbose",
                ),
            ),
            verbosity_level="minimal",
        )
        restored = SoundBank.from_dict(bank.to_dict())
        self.assertEqual(restored.verbosity_level, "minimal")
        self.assertEqual(restored.bindings[0].verbosity, "verbose")

    def test_loader_supplies_defaults_when_fields_missing(self) -> None:
        legacy = {
            "version": SCHEMA_VERSION,
            "voices": [{"id": "v"}],
            "bindings": [
                {
                    "id": "b",
                    "event_type": EventType.COMMAND_FAILED.value,
                    "voice_id": "v",
                    "say_template": "hi",
                }
            ],
        }
        bank = SoundBank.from_dict(legacy)
        self.assertEqual(bank.verbosity_level, "normal")
        self.assertEqual(bank.bindings[0].verbosity, "normal")

    def test_unknown_verbosity_level_raises(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundBank.from_dict({"version": SCHEMA_VERSION, "verbosity_level": "whisper"})

    def test_unknown_binding_verbosity_raises(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundBank.from_dict(
                {
                    "version": SCHEMA_VERSION,
                    "bindings": [
                        {
                            "id": "b",
                            "event_type": EventType.COMMAND_FAILED.value,
                            "say_template": "hi",
                            "verbosity": "whisper",
                        }
                    ],
                }
            )

    def test_bindings_for_filters_by_verbosity(self) -> None:
        bank = self._bank_with_tiered_bindings("minimal")
        ids = [b.id for b in bank.bindings_for(EventType.COMMAND_FAILED.value)]
        self.assertEqual(ids, ["critical"])

        bank = self._bank_with_tiered_bindings("normal")
        ids = [b.id for b in bank.bindings_for(EventType.COMMAND_FAILED.value)]
        self.assertEqual(ids, ["critical"])

        bank = self._bank_with_tiered_bindings("verbose")
        ids = sorted(b.id for b in bank.bindings_for(EventType.COMMAND_FAILED.value))
        self.assertEqual(ids, ["chatty", "critical"])

    def test_editor_view_ignores_verbosity_filter(self) -> None:
        bank = self._bank_with_tiered_bindings("minimal")
        ids = sorted(
            b.id
            for b in bank.bindings_for(
                EventType.COMMAND_FAILED.value,
                respect_verbosity=False,
            )
        )
        self.assertEqual(ids, ["chatty", "critical"])

    def test_with_verbosity_level_returns_updated_copy(self) -> None:
        bank = SoundBank()
        changed = bank.with_verbosity_level("verbose")
        self.assertEqual(changed.verbosity_level, "verbose")
        self.assertEqual(bank.verbosity_level, "normal")

    def test_with_verbosity_level_rejects_unknown(self) -> None:
        with self.assertRaises(SoundBankError):
            SoundBank().with_verbosity_level("whisper")


if __name__ == "__main__":
    unittest.main()
