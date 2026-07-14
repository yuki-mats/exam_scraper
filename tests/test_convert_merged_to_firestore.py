from __future__ import annotations

import unittest

from scripts.convert.convert_merged_to_firestore import (
    get_original_question_body_text,
    original_question_id_for_upload,
    resolve_exam_name_override,
    resolve_law_revision_facts,
)


KOUNIN_SHINRISHI_LIST_GROUP_IDS = (
    "97001",
    "97002",
    "97003",
    "97004",
    "97005",
    "97006",
    "97007",
    "97008",
    "97009",
)


class ConvertMergedToFirestoreTests(unittest.TestCase):
    def test_resolve_exam_name_override_for_kounin_shinrishi_list_groups(self) -> None:
        for list_group_id in KOUNIN_SHINRISHI_LIST_GROUP_IDS:
            with self.subTest(list_group_id=list_group_id):
                self.assertEqual(
                    resolve_exam_name_override(
                        explicit_exam_name=None,
                        qualification=None,
                        list_group_id=list_group_id,
                    ),
                    "公認心理師",
                )

    def test_resolve_exam_name_override_uses_qualification_for_future_groups(self) -> None:
        self.assertEqual(
            resolve_exam_name_override(
                explicit_exam_name=None,
                qualification="kounin-shinrishi",
                list_group_id="future-group",
            ),
            "公認心理師",
        )

    def test_resolve_exam_name_override_prefers_explicit_name(self) -> None:
        self.assertEqual(
            resolve_exam_name_override(
                explicit_exam_name="明示した試験名",
                qualification="kounin-shinrishi",
                list_group_id="97001",
            ),
            "明示した試験名",
        )

    def test_resolve_exam_name_override_does_not_change_unrelated_groups(self) -> None:
        self.assertIsNone(
            resolve_exam_name_override(
                explicit_exam_name=None,
                qualification="2nd-class-kenchikushi",
                list_group_id="85001",
            )
        )

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

    def test_resolve_law_revision_facts_selects_choice_snapshot_verdicts(self) -> None:
        question_body = {
            "lawRevisionFacts": {
                "auditStatus": "same_as_current",
                "examTime": {
                    "correctChoiceText": ["正しい", "間違い"],
                    "verificationStatus": "from_original_answer",
                },
                "current": {
                    "correctChoiceText": ["正しい", "間違い"],
                    "verificationStatus": "verified_current_law",
                },
            }
        }

        self.assertEqual(
            resolve_law_revision_facts(question_body, 1),
            {
                "auditStatus": "same_as_current",
                "examTime": {
                    "correctChoiceText": "間違い",
                    "verificationStatus": "from_original_answer",
                },
                "current": {
                    "correctChoiceText": "間違い",
                    "verificationStatus": "verified_current_law",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
