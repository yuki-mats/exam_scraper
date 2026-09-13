from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts.check import check_gas_shunin_00_source_contract as contract_check


class GasShuninSourceContractCliTests(unittest.TestCase):
    def test_source_accepts_unclassified_type_but_rejects_unknown_value(self):
        source_path = Path("output/gas-shunin-otsu/questions_json/2022/00_source/question_2022_1.json")
        source, _ = contract_check.normalize_question_metadata(
            qualification="gas-shunin-otsu",
            source_path=source_path,
            question={
                "questionBodyText": "記述を選べ。", "choiceTextList": ["記述A"],
                "correctChoiceText": ["正しい"], "examYear": 2022,
                "questionLabel": "問1", "category": "法令",
                "question_url": "https://gassyunin.com/exam/otsu/otsu_2022/#law-q1",
                "public_question_id": "test-id",
            },
        )
        for value, invalid in ((None, False), ("true_false", False), ("group_choice", False), ("unknown", True)):
            question = dict(source)
            if value is not None:
                question["questionType"] = value
            issues = contract_check.validate_question(
                qualification="gas-shunin-otsu", source_path=source_path,
                index=1, question=question,
                source_unique_key_counter=Counter(), review_id_counter=Counter(),
            )
            self.assertEqual("invalid_questionType" in {i["code"] for i in issues}, invalid)

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
