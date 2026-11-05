"""kkex_repair: the KoiCardTexTool damage to the "TextureDictionary" key is put right."""
import unittest

from kkafio.cards.chara.kkex_repair import repair_card

DAMAGED = b"\x87\xb1TextureDictiona\xc6\x00\x00\xce4\x83\x03"
REPAIRED = b"\x87\xb1TextureDictionary\xc6\x00\x00\xce4\x83\x03"


class RepairCardTest(unittest.TestCase):
    def test_damaged_key_is_restored(self):
        self.assertEqual(repair_card(b"head" + DAMAGED + b"tail"), b"head" + REPAIRED + b"tail")

    def test_healthy_card_is_left_alone(self):
        self.assertIsNone(repair_card(b"head" + REPAIRED + b"tail"))

    def test_other_cards_are_left_alone(self):
        self.assertIsNone(repair_card(b"not a card"))

    def test_ambiguous_match_is_left_alone(self):
        self.assertIsNone(repair_card(DAMAGED + DAMAGED))


if __name__ == "__main__":
    unittest.main()
