from __future__ import annotations

import unittest

from scripts.pipeline.apply_gas_shunin_law_explanation_refresh_decision import (
    strip_verdict,
)


class ApplyGasShuninLawExplanationRefreshDecisionTest(unittest.TestCase):
    def test_strip_verdict(self) -> None:
        self.assertEqual(strip_verdict("間違い。選択肢のAが誤り。"), "選択肢のAが誤り。")
        self.assertEqual(strip_verdict("正しい。条文と一致する。"), "条文と一致する。")


if __name__ == "__main__":
    unittest.main()
