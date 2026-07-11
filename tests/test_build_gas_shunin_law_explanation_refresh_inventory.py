from __future__ import annotations

import unittest

from scripts.check.build_gas_shunin_law_explanation_refresh_inventory import (
    has_explicit_wrong_difference,
    law_revision_matches_reference,
)


class BuildGasShuninLawExplanationRefreshInventoryTest(unittest.TestCase):
    def test_specific_wrong_fragment_is_explicit(self) -> None:
        self.assertTrue(
            has_explicit_wrong_difference(
                "間違い。選択肢の「許可」が誤り。法第1条は届出と定めている。"
            )
        )

    def test_legal_basis_alone_is_not_explicit(self) -> None:
        self.assertFalse(
            has_explicit_wrong_difference(
                "間違い。法第1条は、事業の開始前に届け出ると定めている。"
            )
        )

    def test_law_revision_current_must_match_a_direct_reference(self) -> None:
        facts = {"current": {"lawId": "rule", "article": "13"}}
        self.assertTrue(
            law_revision_matches_reference(
                facts,
                [{"lawId": "rule", "article": "13", "verificationStatus": "verified"}],
            )
        )
        self.assertFalse(
            law_revision_matches_reference(
                facts,
                [{"lawId": "act", "article": "2", "verificationStatus": "verified"}],
            )
        )


if __name__ == "__main__":
    unittest.main()
