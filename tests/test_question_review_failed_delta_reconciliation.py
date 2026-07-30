import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.failed_delta_reconciliation import (
    _projected_records,
    _record_scopes,
    _source_records,
    _validate_sidecars,
    _verified_baseline,
)
from tools.question_review_console.qualification_runs import QualificationRunError


class FailedDeltaReconciliationTests(unittest.TestCase):
    def test_current_projection_is_bound_by_ui_question_id(self):
        projected = {
            "choiceTextList": ["公式訂正後A", "公式訂正後B"],
            "correctChoiceText": "公式訂正後A",
        }

        records = _projected_records(
            [
                {
                    "id": "ui-q1",
                    "sourceRecordRef": "source.json#0",
                    "projected": projected,
                }
            ]
        )

        self.assertEqual(records, {"ui-q1": projected})

    def test_current_projection_is_required_for_every_question(self):
        with self.assertRaisesRegex(
            QualificationRunError,
            "現在投影を確認できません",
        ):
            _projected_records(
                [
                    {
                        "id": "ui-q1",
                        "sourceRecordRef": "source.json#0",
                    }
                ]
            )

    def test_current_source_is_bound_by_ui_question_id(self):
        source = {
            "choiceTextList": ["取得元A", "取得元B"],
            "correctChoiceText": "取得元A",
        }

        records = _source_records(
            [
                {
                    "id": "ui-q1",
                    "sourceRecordRef": "source.json#0",
                    "source": source,
                }
            ]
        )

        self.assertEqual(records, {"ui-q1": source})

    def test_accepts_current_sidecar_identity_without_mutation(self):
        relative = (
            "output/sample/review/law_revision_audit/"
            "2026_law_revision_audit.jsonl"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            content = (
                json.dumps(
                    {
                        "schemaVersion": "law-revision-audit/v2",
                        "reviewQuestionId": "source-q1",
                        "sourceQuestionKey": "sample:2026:q1",
                        "sourceRecordRef": "source.json#0",
                        "auditStatus": "not_law_related",
                    }
                )
                + "\n"
            )
            path.write_text(content, encoding="utf-8")

            _validate_sidecars(root, (relative,))

            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_rejects_non_current_sidecar_identity(self):
        relative = (
            "output/sample/review/law_revision_audit/"
            "2026_law_revision_audit.jsonl"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "law-revision-audit/v1",
                        "reviewQuestionId": "q1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "現行identity契約",
            ):
                _validate_sidecars(root, (relative,))

    def test_record_scopes_follow_exact_patch_paths(self):
        patch = (
            "output/sample/questions_json/2026/"
            "21_explanationText_added/source_explanationText_added.json"
        )
        sidecar = (
            "output/sample/review/law_revision_audit/"
            "2026_law_revision_audit.jsonl"
        )
        questions = [
            {"paths": {"patches": [patch]}},
            {"paths": {"patches": []}},
        ]
        aliases = [["q1"], ["q2"]]

        scopes = _record_scopes((patch, sidecar), questions, aliases)

        self.assertEqual(scopes[patch], [["q1"]])
        self.assertEqual(scopes[sidecar], aliases)

    def test_verified_baseline_requires_hash_and_all_paths(self):
        relative = "output/sample/questions_json/2026/patch.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "output/question_review_console/workflow_runs/sample/run-1"
            run_dir.mkdir(parents=True)
            baseline = {"recordSnapshots": {relative: []}}
            raw = json.dumps(baseline).encode()
            baseline_path = run_dir / "baseline.json"
            baseline_path.write_bytes(raw)
            manifest = {
                "runId": "run-1",
                "baselineHash": hashlib.sha256(raw).hexdigest(),
            }

            run_id, loaded, digest = _verified_baseline(
                root,
                [(run_dir / "manifest.json", manifest)],
                (relative,),
            )

        self.assertEqual(run_id, "run-1")
        self.assertEqual(loaded, baseline)
        self.assertEqual(digest, manifest["baselineHash"])

    def test_verified_baseline_rejects_external_path(self):
        relative = "output/sample/questions_json/2026/patch.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            run_dir = root / "output/question_review_console/workflow_runs/sample/run-1"
            run_dir.mkdir(parents=True)
            external = Path(directory) / "baseline.json"
            raw = json.dumps({"recordSnapshots": {relative: []}}).encode()
            external.write_bytes(raw)
            manifest = {
                "runId": "run-1",
                "baselinePath": external.as_posix(),
                "baselineHash": hashlib.sha256(raw).hexdigest(),
            }

            with self.assertRaisesRegex(
                QualificationRunError,
                "検証済みbaselineを確認できません",
            ):
                _verified_baseline(
                    root,
                    [(run_dir / "manifest.json", manifest)],
                    (relative,),
                )


if __name__ == "__main__":
    unittest.main()
