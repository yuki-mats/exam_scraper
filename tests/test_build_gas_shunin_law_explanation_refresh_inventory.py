from __future__ import annotations

import unittest
from unittest.mock import patch

from pathlib import Path

from scripts.check.build_gas_shunin_law_explanation_refresh_inventory import (
    has_explicit_wrong_difference,
    law_revision_matches_reference,
    resolve_explanation_patch_file,
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

    def test_external_primary_matches_by_law_title_and_article(self) -> None:
        facts = {"current": {"lawTitle": "告示A", "article": "3"}}
        self.assertTrue(
            law_revision_matches_reference(
                facts,
                [{"lawTitle": "告示A", "article": "3", "verificationStatus": "verified"}],
            )
        )

    def test_patch_file_resolver_uses_explanation_suffix(self) -> None:
        exact = Path("output/example/question.json")
        suffixed = Path("output/example/question_explanationText_added.json")

        def is_file(path: Path) -> bool:
            return path.as_posix().endswith(suffixed.as_posix())

        with patch.object(Path, "is_file", is_file):
            resolved, error = resolve_explanation_patch_file(exact)

        self.assertIsNone(error)
        self.assertTrue(resolved.endswith(suffixed.as_posix()))


if __name__ == "__main__":
    unittest.main()
