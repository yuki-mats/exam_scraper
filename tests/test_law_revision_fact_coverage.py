from __future__ import annotations

import unittest
from collections import Counter

from scripts.check.check_law_revision_fact_coverage import audit_records


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


if __name__ == "__main__":
    unittest.main()
