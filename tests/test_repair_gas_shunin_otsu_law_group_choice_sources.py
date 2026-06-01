from __future__ import annotations

import unittest

from scripts.pipeline.repair_gas_shunin_otsu_law_group_choice_sources import (
    build_correct_choice_text,
    build_synthetic_snippets,
    extract_choices,
    extract_correct_choice_number,
)


class RepairGasShuninOtsuLawGroupChoiceSourcesTest(unittest.TestCase):
    def test_extract_choices_from_num_choice_box(self) -> None:
        html = """
        <div class="num-choice-box">
          <div>
            <div><strong>(1)</strong> (イ)登録 (ロ)許可</div>
            <div><strong>(2)</strong> (イ)登録 (ロ)登録</div>
            <div><strong>(3)</strong> (イ)許可 (ロ)許可</div>
            <div><strong>(4)</strong> (イ)許可 (ロ)登録</div>
            <div><strong>(5)</strong> (イ)届出 (ロ)登録</div>
          </div>
        </div>
        """
        self.assertEqual(
            extract_choices(html),
            [
                "(イ)登録 (ロ)許可",
                "(イ)登録 (ロ)登録",
                "(イ)許可 (ロ)許可",
                "(イ)許可 (ロ)登録",
                "(イ)届出 (ロ)登録",
            ],
        )

    def test_extract_correct_choice_number(self) -> None:
        html = "<details><h3>🎯 正解: (4)</h3></details>"
        self.assertEqual(extract_correct_choice_number(html), 4)

    def test_build_correct_choice_text(self) -> None:
        self.assertEqual(
            build_correct_choice_text("select_correct", 2, 5),
            ["間違い", "正しい", "間違い", "間違い", "間違い"],
        )
        self.assertEqual(
            build_correct_choice_text("select_incorrect", 5, 5),
            ["正しい", "正しい", "正しい", "正しい", "間違い"],
        )

    def test_build_synthetic_snippets(self) -> None:
        snippets = build_synthetic_snippets(
            choices=["A", "B", "C", "D", "E"],
            correct_choice_number=3,
            question_intent="select_correct",
            refs="法3条、法13条1項",
        )
        self.assertEqual(snippets[2], ["📌 関連: 法3条、法13条1項"])
        self.assertIn("正しくは: C", snippets[0][0])


if __name__ == "__main__":
    unittest.main()
