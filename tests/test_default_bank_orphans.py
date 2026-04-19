"""Regression guard: every binding's voice_id / sound_id resolves.

A binding that references a `voice_id` or `sound_id` that does
not exist in the bank is the worst kind of audio bug — it
fails silently. The engine looks up the id, gets `None`, and
either skips the cue entirely or substitutes a fallback,
neither of which surfaces a user-visible error. The user just
hears nothing, every time, forever.

This test walks the default bank and asserts that every
non-empty `voice_id` and `sound_id` on every binding maps to a
real `Voice` / `SoundRecipe` declared in the same bank. A
new binding that misspells `"narrtor"` will fail this test
before it can ship.

Companion check: every declared voice / sound is referenced by
at least one binding. An unused record bloats the bank and is
nearly always a leftover from a deleted feature; flagging them
makes the binding map auditable in a glance. (Future feature
work that wants to ship a record ahead of its binding can
suppress the check by adding the id to ``_INTENTIONAL_UNUSED``
below with a short reason.)
"""

from __future__ import annotations

import unittest

from asat.default_bank import default_sound_bank


_INTENTIONAL_UNUSED_VOICES: frozenset[str] = frozenset()
_INTENTIONAL_UNUSED_SOUNDS: frozenset[str] = frozenset()


class DefaultBankOrphansTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bank = default_sound_bank()
        self.voice_ids = {voice.id for voice in self.bank.voices}
        self.sound_ids = {sound.id for sound in self.bank.sounds}

    def test_no_binding_references_a_missing_voice(self) -> None:
        orphans = [
            (binding.id, binding.voice_id)
            for binding in self.bank.bindings
            if binding.voice_id and binding.voice_id not in self.voice_ids
        ]
        self.assertEqual(
            orphans,
            [],
            "Bindings reference a voice_id that does not exist in the "
            "bank — the cue would be silent at runtime. Fix the "
            f"voice_id or add the missing voice: {orphans}",
        )

    def test_no_binding_references_a_missing_sound(self) -> None:
        orphans = [
            (binding.id, binding.sound_id)
            for binding in self.bank.bindings
            if binding.sound_id and binding.sound_id not in self.sound_ids
        ]
        self.assertEqual(
            orphans,
            [],
            "Bindings reference a sound_id that does not exist in the "
            "bank — the cue would be silent at runtime. Fix the "
            f"sound_id or add the missing recipe: {orphans}",
        )

    def test_every_voice_is_used_by_at_least_one_binding(self) -> None:
        referenced = {b.voice_id for b in self.bank.bindings if b.voice_id}
        unused = sorted(self.voice_ids - referenced - _INTENTIONAL_UNUSED_VOICES)
        self.assertEqual(
            unused,
            [],
            "Voices declared in the default bank are never referenced "
            "by any binding. Either wire them up or remove them. To "
            "keep an unused voice on purpose, add it to "
            f"_INTENTIONAL_UNUSED_VOICES with a comment: {unused}",
        )

    def test_every_sound_is_used_by_at_least_one_binding(self) -> None:
        referenced = {b.sound_id for b in self.bank.bindings if b.sound_id}
        unused = sorted(self.sound_ids - referenced - _INTENTIONAL_UNUSED_SOUNDS)
        self.assertEqual(
            unused,
            [],
            "Sounds declared in the default bank are never referenced "
            "by any binding. Either wire them up or remove them. To "
            "keep an unused recipe on purpose, add it to "
            f"_INTENTIONAL_UNUSED_SOUNDS with a comment: {unused}",
        )


if __name__ == "__main__":
    unittest.main()
