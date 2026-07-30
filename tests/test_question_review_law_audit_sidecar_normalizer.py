from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.law_audit import valid_v2_audit_row
from tools.question_review_console.law_audit_sidecar_normalizer import (
    normalize_law_audit_sidecars,
)


class LawAuditSidecarNormalizerTests(unittest.TestCase):
    def test_legacy_ui_id_is_replaced_by_canonical_source_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = (
                root
                / "output"
                / "sample"
                / "review"
                / "law_revision_audit"
                / "2026_law_revision_audit.jsonl"
            )
            path.parent.mkdir(parents=True)
            row = valid_v2_audit_row(
                "legacy-ui-id",
                "",
                source_ref="",
                schemaVersion="law-revision-audit/v1",
            )
            path.write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            question = {
                "id": "legacy-ui-id",
                "originalQuestionId": "canonical-review-id",
                "reviewQuestionId": "canonical-review-id",
                "sourceQuestionKey": "sample:2026:q1",
                "sourceRecordRef": "question_2026_1.json#0",
                "projected": {"choiceTextList": ["A"]},
            }

            receipt = normalize_law_audit_sidecars(
                root,
                "sample",
                {"2026": [question]},
            )
            updated = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(receipt["changedRowCount"], 1)
        self.assertEqual(updated["schemaVersion"], "law-revision-audit/v2")
        self.assertEqual(updated["reviewQuestionId"], "canonical-review-id")
        self.assertEqual(updated["sourceQuestionKey"], "sample:2026:q1")
        self.assertEqual(
            updated["sourceRecordRef"],
            "question_2026_1.json#0",
        )
        self.assertEqual(updated["sourceSummary"], row["sourceSummary"])

    def test_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = (
                root
                / "output"
                / "sample"
                / "review"
                / "law_revision_audit"
                / "2026_law_revision_audit.jsonl"
            )
            path.parent.mkdir(parents=True)
            row = valid_v2_audit_row(
                "canonical-review-id",
                "sample:2026:q1",
            )
            path.write_text(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            question = {
                "id": "ui-id",
                "reviewQuestionId": "canonical-review-id",
                "sourceQuestionKey": "sample:2026:q1",
                "sourceRecordRef": "question_2026_1.json#0",
                "projected": {"choiceTextList": ["A"]},
            }

            first = normalize_law_audit_sidecars(
                root,
                "sample",
                {"2026": [question]},
            )
            second = normalize_law_audit_sidecars(
                root,
                "sample",
                {"2026": [question]},
            )

        self.assertEqual(first["changedRowCount"], 0)
        self.assertEqual(second["changedRowCount"], 0)
