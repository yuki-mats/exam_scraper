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
                    "lawRevisionFacts": valid_facts("hold"),
                }
            ],
            require_all_law_related=True,
            fail_on_hold=True,
            require_evidence_summary=True,
        )

        self.assertEqual(counts, Counter({"law_related": 1, "with_facts": 1, "hold": 1}))
        self.assertTrue(any("auditStatus is hold" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
