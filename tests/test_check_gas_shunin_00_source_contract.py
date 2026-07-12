from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.check import check_gas_shunin_00_source_contract as contract_check


class GasShuninSourceContractCliTests(unittest.TestCase):
    def test_fix_requires_list_group_ids(self) -> None:
        with patch(
            "sys.argv",
            [
                "check_gas_shunin_00_source_contract.py",
                "--qualifications",
                "gas-shunin-kou",
                "--fix",
            ],
        ):
            self.assertEqual(contract_check.main(), 2)

    def test_parse_list_group_ids(self) -> None:
        with patch(
            "sys.argv",
            [
                "check_gas_shunin_00_source_contract.py",
                "--qualifications",
                "gas-shunin-kou",
                "--list-group-ids",
                "2018",
                "2017",
            ],
        ):
            args = contract_check.parse_args()

        self.assertEqual(args.list_group_ids, ["2018", "2017"])


if __name__ == "__main__":
    unittest.main()
