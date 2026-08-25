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


if __name__ == "__main__":
    unittest.main()
