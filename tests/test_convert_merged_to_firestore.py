from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.convert.convert_merged_to_firestore import (
    convert_question_to_firestore,
    convert_merged_to_firestore,
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
    def test_derived_statement_ids_are_new_and_original_body_remains_visible(self) -> None:
        body = "組合せを選べ。\nA 原文一。\nB 原文二。"
        question = {
            "original_question_id": "original-q1",
            "questionBodyText": body,
            "sourceUniqueKeys": [
                "sample:2026:q001:aggregate-statement:1:aaaaaaaaaaaaaaaa",
                "sample:2026:q001:aggregate-statement:2:bbbbbbbbbbbbbbbb",
            ],
            "choiceTextList": ["A 原文一。", "B 原文二。"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["正しい。", "間違い。"],
            "questionType": "true_false",
            "examYear": 2026,
            "questionLabel": "問1",
        }

        converted = convert_question_to_firestore(question)

        self.assertEqual(len(converted), 2)
        self.assertEqual(converted[0]["originalQuestionId"], "original-q1")
        self.assertNotEqual(converted[0]["questionId"], "original-q1")
        self.assertEqual(converted[0]["originalQuestionBodyText"], body)
        self.assertIn(body.replace("\n", ""), converted[0]["questionText"])
        self.assertIn("[quote]A 原文一。[/quote]", converted[0]["questionText"])

    def test_single_choice_new_empty_suggestions_do_not_republish_legacy_flat_data(
        self,
    ) -> None:
        legacy_details = [
            {"question": f"旧質問{i}", "answer": f"旧回答{i}"}
            for i in range(5)
        ]
        question = {
            "sourceQuestionKey": "sample:2026:q1",
            "original_question_id": "q1",
            "questionBodyText": "最も近い値を選べ。",
            "choiceTextList": ["1", "2", "3", "4", "5"],
            "correctChoiceText": [
                "間違い",
                "間違い",
                "間違い",
                "正しい",
                "間違い",
            ],
            "explanationText": ["計算すると4となる。"],
            "questionType": "single_choice",
            "questionIntent": "select_correct",
            "answer_result_text": "正解は4です。",
            "suggestedQuestions": [item["question"] for item in legacy_details],
            "suggestedQuestionDetails": legacy_details,
            "suggestedQuestionDetailsByChoice": [],
            "examYear": 2026,
            "questionLabel": "問1",
        }

        converted = convert_question_to_firestore(question)[0]

        self.assertNotIn("suggestedQuestions", converted)
        self.assertNotIn("suggestedQuestionDetails", converted)

    def test_question_set_id_comes_only_from_each_merged_record(self) -> None:
        def record(source_key: str, question_set_id: str) -> dict:
            return {
                "sourceQuestionKey": source_key,
                "sourceUniqueKeys": [f"{source_key}:choice-1"],
                "original_question_id": "duplicate-review-id",
                "questionBodyText": "正しいものはどれか。",
                "choiceTextList": ["選択肢"],
                "correctChoiceText": ["正しい"],
                "explanationText": ["正しい。確認済みです。"],
                "questionType": "true_false",
                "questionIntent": "select_correct",
                "answer_result_text": "正解は1です。",
                "questionSetId": question_set_id,
                "examYear": 2026,
                "questionLabel": "問1",
            }

        with tempfile.TemporaryDirectory() as directory:
            group_dir = (
                Path(directory)
                / "output"
                / "sample"
                / "questions_json"
                / "2026"
            )
            merged_dir = group_dir / "30_merged_2"
            merged_dir.mkdir(parents=True)
            input_path = merged_dir / "question_2026_merged.json"
            input_path.write_text(
                json.dumps(
                    {
                        "list_group_id": "2026",
                        "question_bodies": [
                            record("sample:first", "set-from-first-record"),
                            record("sample:second", ""),
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            patch_dir = group_dir / "22_questionSetId_linked"
            patch_dir.mkdir()
            (patch_dir / "aggregate_questionSetId_linked.json").write_text(
                json.dumps(
                    [
                        {
                            "original_question_id": "duplicate-review-id",
                            "questionSetId": "must-not-be-read",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_path = group_dir / "40_convert" / "converted.json"
            output_path.parent.mkdir()
            output = convert_merged_to_firestore(input_path, output_path)

        by_id = {question["questionId"]: question for question in output["questions"]}
        self.assertEqual(
            by_id["sample-first-choice-1"]["questionSetId"],
            "set-from-first-record",
        )
        self.assertEqual(by_id["sample-second-choice-1"]["questionSetId"], "")

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
