import unittest
from contract import EXPAND_IDS, ORACLE_WORDS, SCREEN_IDS, items

class ContractTest(unittest.TestCase):
    def test_fixed_sets(self):
        self.assertEqual(10, len(SCREEN_IDS)); self.assertEqual(32, len(EXPAND_IDS))
        self.assertEqual(SCREEN_IDS, [x["id"] for x in items(SCREEN_IDS)])
        self.assertTrue(set(SCREEN_IDS) <= set(EXPAND_IDS))
    def test_inputs_have_no_oracle_keys(self):
        text = str(items(EXPAND_IDS))
        for word in ORACLE_WORDS: self.assertNotIn(word, text)

if __name__ == "__main__": unittest.main()
