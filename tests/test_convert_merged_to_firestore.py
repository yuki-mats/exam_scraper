from __future__ import annotations

import unittest

from scripts.convert.convert_merged_to_firestore import (
    get_original_question_body_text,
    original_question_id_for_upload,
    resolve_law_revision_facts,
)


class ConvertMergedToFirestoreTests(unittest.TestCase):
    def test_get_original_question_body_text_falls_back_to_question_body_text(self) -> None:
        question_body = {
            "questionBodyText": "  元の問題文として使う本文  ",
            "originalQuestionBodyText": "",
            "original_question_body_text": None,
        }

        self.assertEqual(
            get_original_question_body_text(question_body),
            "元の問題文として使う本文",
        )

    def test_original_question_id_ignores_firestore_review_key_when_source_id_exists(self) -> None:
        question_body = {
            "original_question_id": "firestore:doc-1,doc-2",
            "originalQuestionId": "gasushunin-koushu-gizyutsu-2019-1",
            "firestoreQuestionIds": ["doc-1", "doc-2"],
        }

        self.assertEqual(
            original_question_id_for_upload(question_body),
            "gasushunin-koushu-gizyutsu-2019-1",
        )

    def test_original_question_id_prefers_explicit_upload_original_question_id(self) -> None:
        question_body = {
            "uploadOriginalQuestionId": "stable-upload-id",
            "original_question_id": "firestore:doc-1,doc-2",
            "originalQuestionId": "source-id",
        }

        self.assertEqual(original_question_id_for_upload(question_body), "stable-upload-id")

    def test_resolve_law_revision_facts_removes_null_optional_values(self) -> None:
        question_body = {
            "lawRevisionFacts": {
                "auditStatus": "same_as_current",
                "current": {
                    "lawId": "329AC0000000051",
                    "article": "1",
                    "item": None,
                    "supportingRefs": [{"lawId": "ignored"}],
                },
                "evidenceSummary": {
                    "refs": [
                        {
                            "refId": "current:law:1",
                            "paragraph": "",
                            "item": None,
                        }
                    ],
                },
            }
        }

        self.assertEqual(
            resolve_law_revision_facts(question_body),
            {
                "auditStatus": "same_as_current",
                "current": {
                    "lawId": "329AC0000000051",
                    "article": "1",
                },
                "evidenceSummary": {
                    "refs": [
                        {
                            "refId": "current:law:1",
                        }
                    ],
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
