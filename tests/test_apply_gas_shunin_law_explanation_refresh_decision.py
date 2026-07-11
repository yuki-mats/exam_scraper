from __future__ import annotations

import unittest

from scripts.pipeline.apply_gas_shunin_law_explanation_refresh_decision import (
    basis_api_url,
    basis_label,
    basis_references,
    correction_basis_by_choice,
    strip_verdict,
    update_law_references,
    update_law_revision_facts,
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

    def test_basis_label_formats_appendix_without_article_affixes(self) -> None:
        self.assertEqual(
            basis_label(
                {
                    "lawTitle": "施行令",
                    "article": "別表第二",
                    "item": "1",
                }
            ),
            "施行令別表第二第1号",
        )

    def test_basis_label_formats_table_item_without_item_affixes(self) -> None:
        self.assertEqual(
            basis_label(
                {
                    "lawTitle": "省令",
                    "article": "51",
                    "paragraph": "1",
                    "item": "表（3）",
                }
            ),
            "省令第51条第1項の表（3）",
        )

    def test_basis_label_formats_article_suffix_after_article_marker(self) -> None:
        self.assertEqual(
            basis_label(
                {
                    "lawTitle": "ガス事業法",
                    "article": "56の2",
                    "paragraph": "1",
                }
            ),
            "ガス事業法第56条の2第1項",
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

    def test_update_law_references_initializes_empty_choice_slot(self) -> None:
        entry = {"lawReferences": [[]]}
        correction = {
            "basisByChoiceIndex": {
                "0": {
                    "sourceType": "external_primary",
                    "source": "official_pdf",
                    "sourceUrl": "https://example.com/question.pdf",
                    "lawTitle": "公式問題",
                    "article": "問1選択肢1",
                    "articleTextHash": "question-hash",
                }
            }
        }

        update_law_references(
            entry,
            explanations=["正しい。公式問題と一致する。"],
            correction=correction,
            reviewed_at="2026-07-12T00:00:00+09:00",
        )

        reference = entry["lawReferences"][0][0]
        self.assertEqual(reference["choiceIndex"], 0)
        self.assertEqual(reference["source"], "official_pdf")
        self.assertEqual(reference["appLinkMode"], "source_url")
        self.assertTrue(reference["externalPrimarySource"])

    def test_update_law_revision_facts_initializes_null_container(self) -> None:
        entry = {"lawRevisionFacts": None}
        correction = {
            "basisByChoiceIndex": {
                "0": {
                    "sourceType": "external_primary",
                    "source": "official_pdf",
                    "sourceUrl": "https://example.com/question.pdf",
                    "lawTitle": "公式問題",
                    "article": "問1選択肢1",
                    "articleTextHash": "question-hash",
                }
            }
        }

        update_law_revision_facts(
            entry,
            explanations=["正しい。公式問題と一致する。"],
            correction=correction,
            reviewed_at="2026-07-12T00:00:00+09:00",
        )

        fact = entry["lawRevisionFacts"][0]
        self.assertEqual(fact["current"]["sourceType"], "external_primary")
        self.assertEqual(fact["current"]["source"], "official_pdf")
        self.assertEqual(fact["reviewState"], "refresh_reviewed")


if __name__ == "__main__":
    unittest.main()
