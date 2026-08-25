import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))
from benchmark_contract import ALL_IDS, ORACLE_KEYS  # noqa: E402


class ContractTest(unittest.TestCase):
    def test_fixed_set_is_unique_and_36(self):
        self.assertEqual(36, len(ALL_IDS))
        self.assertEqual(36, len(set(ALL_IDS)))

    def test_oracle_keys_are_not_allowed_manifest_keys(self):
        allowed = {"id", "year", "question", "choices", "image", "targets"}
        self.assertTrue(allowed.isdisjoint(ORACLE_KEYS))

    def test_fixed_set_v2_replacements(self):
        self.assertIn("4ef67113801362d9", ALL_IDS)
        self.assertIn("ef0992b6887ec00b", ALL_IDS)
        self.assertNotIn("dfb3fe84e07f47f9", ALL_IDS)
        self.assertNotIn("1ebaca9b85c6dd6e", ALL_IDS)
        self.assertNotIn("d732ddbaf0d4f522", ALL_IDS)


if __name__ == "__main__":
    unittest.main()
