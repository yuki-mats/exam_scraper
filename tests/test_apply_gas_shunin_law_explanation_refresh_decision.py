from __future__ import annotations

import unittest

from scripts.pipeline.apply_gas_shunin_law_explanation_refresh_decision import (
    basis_api_url,
    basis_references,
    correction_basis_by_choice,
    strip_verdict,
    validate_basis,
)


class ApplyGasShuninLawExplanationRefreshDecisionTest(unittest.TestCase):
    def test_strip_verdict(self) -> None:
        self.assertEqual(strip_verdict("間違い。選択肢のAが誤り。"), "選択肢のAが誤り。")
        self.assertEqual(strip_verdict("正しい。条文と一致する。"), "条文と一致する。")

    def test_correction_basis_by_choice_supports_multiple_articles(self) -> None:
        correction = {
            "basisByChoiceIndex": {
                "0": {
                    "lawId": "law-a",
                    "lawTitle": "法令A",
                    "article": "24",
                    "paragraph": "1",
                    "articleTextHash": "hash-24",
                },
                "1": {
                    "lawId": "law-a",
                    "lawTitle": "法令A",
                    "article": "25",
                    "paragraph": "1",
                    "articleTextHash": "hash-25",
                },
            }
        }

        bases = correction_basis_by_choice(correction, 2)

        self.assertEqual([basis["article"] for basis in bases], ["24", "25"])

    def test_correction_basis_by_choice_preserves_legacy_schema(self) -> None:
        correction = {
            "lawId": "law-a",
            "lawTitle": "法令A",
            "article": "13",
            "paragraph": "1",
            "itemsByChoiceIndex": {"0": "13", "1": "14"},
            "articleTextHash": "hash-13",
        }

        bases = correction_basis_by_choice(correction, 2)

        self.assertEqual([basis["item"] for basis in bases], ["13", "14"])

    def test_basis_references_expands_multiple_paragraphs(self) -> None:
        basis = {
            "lawId": "law-a",
            "lawTitle": "法令A",
            "article": "32",
            "articleTextHash": "hash-32",
            "references": [{"paragraph": "3"}, {"paragraph": "4"}],
        }

        references = basis_references(basis)

        self.assertEqual(
            [reference["paragraph"] for reference in references], ["3", "4"]
        )
        self.assertTrue(all(reference["article"] == "32" for reference in references))

    def test_basis_api_url_preserves_lawdata_url_for_appendix(self) -> None:
        api_url = basis_api_url(
            {
                "lawId": "law-a",
                "article": "別表第二",
                "apiUrl": "https://laws.e-gov.go.jp/api/1/lawdata/law-a",
            }
        )

        self.assertEqual(
            api_url, "https://laws.e-gov.go.jp/api/1/lawdata/law-a"
        )

    def test_validate_basis_accepts_external_primary_without_law_id(self) -> None:
        validate_basis(
            {
                "sourceType": "external_primary",
                "sourceUrl": "https://example.com/official.pdf",
                "lawTitle": "告示A",
                "article": "3",
                "articleTextHash": "hash-3",
            },
            0,
        )


if __name__ == "__main__":
    unittest.main()
