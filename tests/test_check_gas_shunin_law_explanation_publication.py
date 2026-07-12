import unittest

from scripts.check.check_gas_shunin_law_explanation_publication import (
    review_correct_choice_labels,
    strip_explanation_prefix,
    valid_explanation_prefix,
    valid_external_primary_reference,
)


class CheckGasShuninLawExplanationPublicationTest(unittest.TestCase):
    def test_prefers_confirmed_patched_correct_choice_labels(self) -> None:
        self.assertEqual(
            review_correct_choice_labels(
                {
                    "sourceCorrectChoiceText": ["正しい", "間違い"],
                    "patchedCorrectChoiceText": ["間違い", "正しい"],
                }
            ),
            ["間違い", "正しい"],
        )

    def test_accepts_only_the_two_supported_verdict_prefixes(self) -> None:
        self.assertTrue(valid_explanation_prefix("正しい", "正しい。根拠です。"))
        self.assertTrue(valid_explanation_prefix("正しい", "正解。根拠です。"))
        self.assertTrue(valid_explanation_prefix("間違い", "間違い。根拠です。"))
        self.assertTrue(valid_explanation_prefix("間違い", "不正解。根拠です。"))
        self.assertFalse(valid_explanation_prefix("正しい", "妥当。根拠です。"))
        self.assertFalse(valid_explanation_prefix("間違い", "誤り。根拠です。"))

    def test_strips_supported_wrong_answer_prefixes(self) -> None:
        self.assertEqual(strip_explanation_prefix("間違い", "間違い。理由"), "理由")
        self.assertEqual(strip_explanation_prefix("間違い", "不正解。理由"), "理由")

    def test_accepts_only_allowlisted_official_external_sources(self) -> None:
        for source_url in (
            "https://www.meti.go.jp/policy/example.pdf",
            "https://www.jia-page.or.jp/files/user/doc/exam/q_otsu_r6.pdf",
            "https://laws.e-gov.go.jp/law/345M50000400097",
            "https://web.archive.org/web/20230809110023id_/https://www.jia-page.or.jp/files/user/doc/exam/q_otsu_r2.pdf",
        ):
            with self.subTest(source_url=source_url):
                self.assertTrue(
                    valid_external_primary_reference(
                        {"externalPrimarySource": True, "sourceUrl": source_url}
                    )
                )

        self.assertFalse(
            valid_external_primary_reference(
                {
                    "externalPrimarySource": False,
                    "sourceUrl": "https://www.jia-page.or.jp/files/user/doc/exam/q_otsu_r6.pdf",
                }
            )
        )
        self.assertFalse(
            valid_external_primary_reference(
                {"externalPrimarySource": True, "sourceUrl": "https://example.com/source.pdf"}
            )
        )
        self.assertFalse(
            valid_external_primary_reference(
                {
                    "externalPrimarySource": True,
                    "sourceUrl": "https://web.archive.org/web/20230809110023id_/https://example.com/fake.pdf",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
