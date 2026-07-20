from __future__ import annotations

import unittest
from collections import Counter

from scripts.check.check_law_revision_fact_coverage import (
    audit_question_level_law_evidence,
    audit_records,
    select_original_questions,
)


def valid_facts(status: str = "same_as_current") -> dict[str, object]:
    return {
        "auditStatus": status,
        "reviewState": "primary_verified",
        "current": {
            "correctChoiceText": "正しい",
            "lawId": "325AC0000000201",
            "lawTitle": "建築基準法",
            "article": "6",
            "referenceDate": "2026-07-05",
            "verificationStatus": "verified",
        },
        "evidenceSummary": {
            "verdict": "correct",
            "refs": [
                {
                    "refId": "current_basis_1",
                    "lawTimeScope": "current",
                    "relation": "current_basis",
                    "primaryBasis": True,
                    "lawId": "325AC0000000201",
                    "lawTitle": "建築基準法",
                    "article": "6",
                }
            ],
        },
    }


class LawRevisionFactCoverageTests(unittest.TestCase):
    def test_selects_only_requested_original_questions(self) -> None:
        records = [
            {"original_question_id": "q1"},
            {"originalQuestionId": "q2"},
        ]

        selected, missing = select_original_questions(records, ["q1", "q3"])

        self.assertEqual(selected, [records[0]])
        self.assertEqual(missing, ["q3"])

    def test_can_require_verified_references_and_public_evidence(self) -> None:
        errors = audit_question_level_law_evidence(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": "正しい",
                    "isLawRelated": True,
                    "lawReferences": [
                        {
                            "lawId": "325AC0000000201",
                            "lawTitle": "建築基準法",
                            "article": "6",
                            "verificationStatus": "pending",
                        }
                    ],
                    "lawRevisionFacts": valid_facts(),
                    "explanationText": "正しい。",
                    "suggestedQuestions": ["理由は何か。"],
                    "suggestedQuestionDetails": [
                        {"question": "理由は何か。", "answer": "内容が合うため。"}
                    ],
                }
            ],
            require_verified_law_references=True,
            require_public_law_evidence=True,
        )

        self.assertTrue(any("no verified lawReferences" in error for error in errors))
        self.assertTrue(any("concrete law evidence anchor" in error for error in errors))

    def test_question_level_evidence_combines_firestore_choice_records(self) -> None:
        records = [
            {
                "originalQuestionId": "q1",
                "isLawRelated": True,
                "lawReferences": [
                    {
                        "lawTitle": "建築基準法",
                        "article": "6",
                        "verificationStatus": "verified",
                    }
                ],
                "lawRevisionFacts": valid_facts(),
                "explanationText": "正しい。",
                "suggestedQuestions": ["どの条文で判断しますか。"],
                "suggestedQuestionDetails": [
                    {"answer": "建築基準法第6条で判断します。"}
                ],
            },
            {
                "originalQuestionId": "q1",
                "isLawRelated": True,
                "lawReferences": [],
                "lawRevisionFacts": valid_facts(),
                "explanationText": "間違い。",
                "suggestedQuestions": [],
                "suggestedQuestionDetails": [],
            },
        ]

        errors = audit_question_level_law_evidence(
            records,
            require_verified_law_references=True,
            require_public_law_evidence=True,
        )

        self.assertEqual(errors, [])

    def test_question_level_requires_references_from_at_least_one_choice(self) -> None:
        errors = audit_question_level_law_evidence(
            [
                {
                    "originalQuestionId": "q1",
                    "isLawRelated": True,
                    "lawReferences": [],
                },
                {
                    "originalQuestionId": "q1",
                    "isLawRelated": True,
                    "lawReferences": [],
                },
            ],
            require_law_references=True,
            require_verified_law_references=False,
            require_public_law_evidence=False,
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("missing lawReferences", errors[0])

    def test_reports_missing_facts_for_law_related_record(self) -> None:
        errors, counts = audit_records(
            [
                {
                    "questionId": "q1",
                    "isLawRelated": True,
                    "lawGroundedExplanationNotNeeded": False,
                    "lawReferences": [
                        {
                            "lawId": "325AC0000000201",
                            "article": "6",
                        }
                    ],
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=False,
            require_law_references=False,
        )

        self.assertEqual(counts["law_related"], 1)
        self.assertEqual(counts["missing"], 1)
        self.assertTrue(any("missing lawRevisionFacts" in error for error in errors))

    def test_counts_valid_statuses_and_can_fail_on_hold(self) -> None:
        errors, counts = audit_records(
            [
                {
                    "questionId": "q1",
                    "isLawRelated": True,
                    "lawGroundedExplanationNotNeeded": False,
                    "lawReferences": [
                        {
                            "lawId": "325AC0000000201",
                            "article": "6",
                        }
                    ],
                    "lawRevisionFacts": valid_facts("hold"),
                }
            ],
            require_all_law_related=True,
            fail_on_hold=True,
            require_evidence_summary=True,
            require_law_references=False,
        )

        self.assertEqual(counts, Counter({"law_related": 1, "with_facts": 1, "hold": 1}))
        self.assertTrue(any("auditStatus is hold" in error for error in errors))

    def test_can_require_law_references_for_law_related_records(self) -> None:
        errors, counts = audit_records(
            [
                {
                    "questionId": "q1",
                    "isLawRelated": True,
                    "lawGroundedExplanationNotNeeded": False,
                    "lawRevisionFacts": valid_facts("same_as_current"),
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
        )

        self.assertEqual(counts["missing_law_references"], 1)
        self.assertTrue(any("missing lawReferences" in error for error in errors))

    def test_can_require_current_verdict_to_match_firestore_verdict(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"].pop("correctChoiceText")
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": "間違い",
                    "isLawRelated": True,
                    "lawReferences": [{"lawId": "law1"}],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
        )

        self.assertTrue(any("現行法監査" in error for error in errors))

    def test_accepts_matching_current_verdict(self) -> None:
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": "正しい",
                    "isLawRelated": True,
                    "lawReferences": [{"lawId": "law1"}],
                    "lawRevisionFacts": valid_facts("same_as_current"),
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
        )

        self.assertEqual(errors, [])

    def test_accepts_matching_merged_question_verdicts(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["正しい", "間違い"]
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertEqual(errors, [])

    def test_rejects_merged_verdict_count_mismatch(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["正しい"]
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertTrue(any("lawRevisionFacts[1] is invalid" in error for error in errors))

    def test_rejects_invalid_merged_verdict_item(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["正しい", ""]
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertTrue(any("lawRevisionFacts[1] is invalid" in error for error in errors))

    def test_rejects_merged_verdict_value_mismatch(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["間違い", "間違い"]
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertTrue(any("一致しません" in error for error in errors))

    def test_keeps_firestore_facts_scalar_only(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["正しい"]
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": "正しい",
                    "isLawRelated": True,
                    "lawReferences": [{"lawId": "law1"}],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
        )

        self.assertTrue(any("lawRevisionFacts[1] is invalid" in error for error in errors))

    def test_keeps_firestore_law_revision_facts_as_object(self) -> None:
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": "正しい",
                    "isLawRelated": True,
                    "lawReferences": [{"lawId": "law1"}],
                    "lawRevisionFacts": [valid_facts("same_as_current")],
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
        )

        self.assertTrue(
            any("must be an object for Firestore records" in error for error in errors)
        )

    def test_rejects_non_object_in_merged_facts_list(self) -> None:
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": [valid_facts("same_as_current"), "invalid"],
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=False,
            allow_question_level_choice_verdicts=True,
        )

        self.assertTrue(
            any("must be a non-empty list of objects" in error for error in errors)
        )

    def test_rejects_merged_exam_time_verdict_count_mismatch(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["正しい", "間違い"]
        facts["examTime"] = {"correctChoiceText": ["正しい"]}
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertTrue(any("lawRevisionFacts[1] is invalid" in error for error in errors))

    def test_rejects_unknown_key_after_merged_normalization(self) -> None:
        facts = valid_facts("same_as_current")
        facts["current"]["correctChoiceText"] = ["正しい", "間違い"]
        facts["unexpected"] = "must not be discarded"
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": facts,
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertTrue(any("lawRevisionFacts[1] is invalid" in error for error in errors))

    def test_accepts_existing_per_choice_facts_list(self) -> None:
        correct_facts = valid_facts("same_as_current")
        incorrect_facts = valid_facts("same_as_current")
        incorrect_facts["current"]["correctChoiceText"] = "間違い"
        errors, _ = audit_records(
            [
                {
                    "questionId": "q1",
                    "correctChoiceText": ["正しい", "間違い"],
                    "isLawRelated": True,
                    "lawReferences": [[{"lawId": "law1"}], [{"lawId": "law1"}]],
                    "lawRevisionFacts": [correct_facts, incorrect_facts],
                }
            ],
            require_all_law_related=True,
            fail_on_hold=False,
            require_evidence_summary=True,
            require_law_references=True,
            require_current_correct_choice=True,
            allow_question_level_choice_verdicts=True,
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
