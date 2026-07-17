from __future__ import annotations

import unittest

from scripts.common.law_audit_sidecar_contract import (
    LAW_AUDIT_REVIEW_STATES,
    LAW_AUDIT_SCHEMA_V2,
    LAW_AUDIT_STATUSES,
    law_audit_sidecar_metadata_errors,
    normalize_audit_review_state,
)
from tests.support.law_audit import valid_v2_audit_row


class LawAuditSidecarContractTests(unittest.TestCase):
    def test_shared_sidecar_contract_defines_v2_vocabulary(self) -> None:
        self.assertEqual(LAW_AUDIT_SCHEMA_V2, "law-revision-audit/v2")
        self.assertIn("updated_to_current_law", LAW_AUDIT_STATUSES)
        self.assertIn("tertiary_verified", LAW_AUDIT_REVIEW_STATES)
        self.assertEqual(
            normalize_audit_review_state(" Primary-Verified "),
            "primary_checked",
        )

    def test_v2_metadata_requires_notice_for_current_law_update(self) -> None:
        row = valid_v2_audit_row(
            "q1",
            "sample:2026:q1",
            auditStatus="updated_to_current_law",
            userVisibleNoticeRequired=False,
            noticeReason="",
        )

        errors = law_audit_sidecar_metadata_errors(row, expected_choice_count=1)

        self.assertTrue(
            any("userVisibleNoticeRequired" in error for error in errors),
            errors,
        )
        self.assertTrue(any("noticeReason" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
