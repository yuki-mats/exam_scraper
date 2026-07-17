from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.check.check_law_revision_fact_coverage import (
    latest_firestore_file,
    latest_merged_files,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "pipeline" / "prepare_firestore_upload.py"
STRICT_CHECKER = (
    REPO_ROOT / "scripts" / "check" / "check_law_revision_fact_coverage.py"
)
STRICT_LAW_FLAGS = (
    "--require-all-law-related",
    "--fail-on-hold",
    "--require-evidence-summary",
    "--require-law-references",
    "--require-current-correct-choice",
    "--require-verified-law-references",
    "--require-public-law-evidence",
    "--original-question-id",
    "e2e-law-question-1",
)


def source_payload() -> dict[str, object]:
    return {
        "question_bodies": [
            {
                "original_question_id": "e2e-law-question-1",
                "public_question_id": "e2e-law-question-1",
                "question_url": "https://example.com/e2e-law-question-1",
                "list_group_id": "2026",
                "examYear": 2026,
                "examLabel": "2026年度",
                "questionLabel": "問1",
                "qualificationName": "E2E試験",
                "questionBodyText": "建築確認に関する記述の正誤を判定せよ。",
                "choiceTextList": ["基準に合う。", "基準に合わない。"],
                "questionType": "true_false",
                "questionIntent": "select_correct",
                "correctChoiceText": ["正しい", "間違い"],
                "answer_result_text": "正解は 1 です。",
                "answer_result_inferred_correct_choice_numbers": [1],
                "explanationText": ["変更前の解説1", "変更前の解説2"],
                "suggestedQuestions": [],
                "suggestedQuestionDetails": [],
                "lawReferences": [],
                "isLawRelated": False,
                "lawGroundedExplanationNotNeeded": True,
                "questionSetId": "e2e-set",
                "questionImageStorageUrls": [],
                "originalQuestionChoiceImageUrls": [[], []],
            }
        ]
    }


def law_patch() -> list[dict[str, object]]:
    law_id = "325AC0000000201"
    law_title = "建築基準法"
    reference_date = "2026-07-17"
    references = [
        [
            {
                "role": "current_basis",
                "scope": "choice",
                "choiceIndex": 0,
                "lawId": law_id,
                "lawTitle": law_title,
                "article": "6",
                "referenceDate": reference_date,
                "verificationStatus": "verified",
                "source": "egov_xml",
                "comparisonStatus": "same_as_current",
            }
        ],
        [],
    ]
    return [
        {
            "original_question_id": "e2e-law-question-1",
            "question_url": "https://example.com/e2e-law-question-1",
            "explanationText": [
                "正しい。建築基準法第6条の基準に合致する。",
                "間違い。条件に合致しない。",
            ],
            "suggestedQuestions": ["建築確認の根拠条文は何か。"],
            "suggestedQuestionDetails": [
                {
                    "question": "建築確認の根拠条文は何か。",
                    "answer": "該当する条文の基準で判断する。",
                }
            ],
            "lawReferences": references,
            "lawRevisionFacts": {
                "auditStatus": "same_as_current",
                "reviewState": "primary_verified",
                "current": {
                    "correctChoiceText": ["正しい", "間違い"],
                    "lawId": law_id,
                    "lawTitle": law_title,
                    "article": "6",
                    "referenceDate": reference_date,
                    "verificationStatus": "verified",
                },
                "evidenceSummary": {
                    "verdict": "correct",
                    "refs": [
                        {
                            "refId": "current_basis_article_6",
                            "lawTimeScope": "current",
                            "relation": "current_basis",
                            "primaryBasis": True,
                            "lawId": law_id,
                            "lawTitle": law_title,
                            "article": "6",
                        }
                    ],
                },
            },
            "isLawRelated": True,
            "lawGroundedExplanationNotNeeded": False,
        }
    ]


class QuestionReviewArtifactSyncE2ETests(unittest.TestCase):
    def run_command(self, *command: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"command failed: {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )
        return result

    def test_real_pipeline_applies_patch_and_produces_strict_valid_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = (
                Path(directory)
                / "output"
                / "sample-qualification"
                / "questions_json"
            )
            group_dir = base_dir / "2026"
            source_path = group_dir / "00_source" / "question_2026_1.json"
            patch_path = (
                group_dir
                / "21_explanationText_added"
                / "question_2026_1_merged_explanationText_added.json"
            )
            source_path.parent.mkdir(parents=True)
            patch_path.parent.mkdir(parents=True)

            source_path.write_text(
                json.dumps(source_payload(), ensure_ascii=False), encoding="utf-8"
            )
            patch = law_patch()
            patch_path.write_text(
                json.dumps(patch, ensure_ascii=False), encoding="utf-8"
            )
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

            pipeline = self.run_command(
                sys.executable,
                str(PIPELINE_SCRIPT),
                "2026",
                "--base-dir",
                str(base_dir),
                "--skip-update-category-counts",
                "--upload-dry-run",
            )
            self.assertIn("[DRY RUN] 実際のアップロードは行いません。", pipeline.stdout)

            for stage in ("merged", "firestore"):
                self.run_command(
                    sys.executable,
                    str(STRICT_CHECKER),
                    "--list-group-dir",
                    str(group_dir),
                    "--stage",
                    stage,
                    *STRICT_LAW_FLAGS,
                )

            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash
            )

            merged_paths = latest_merged_files(group_dir)
            self.assertEqual(len(merged_paths), 1)
            merged_question = json.loads(
                merged_paths[0].read_text(encoding="utf-8")
            )["question_bodies"][0]
            self.assertEqual(
                merged_question["explanationText"], patch[0]["explanationText"]
            )
            self.assertEqual(
                merged_question["lawRevisionFacts"]["current"]["correctChoiceText"],
                ["正しい", "間違い"],
            )

            firestore_path = latest_firestore_file(group_dir)
            self.assertIsNotNone(firestore_path)
            assert firestore_path is not None
            firestore_payload = json.loads(firestore_path.read_text(encoding="utf-8"))
            firestore_questions = firestore_payload["questions"]
            self.assertEqual(len(firestore_questions), 2)
            self.assertEqual(
                [question["correctChoiceText"] for question in firestore_questions],
                ["正しい", "間違い"],
            )
            self.assertEqual(
                [
                    question["lawRevisionFacts"]["current"]["correctChoiceText"]
                    for question in firestore_questions
                ],
                ["正しい", "間違い"],
            )

            upload_paths = list(
                (base_dir / "upload_to_firestore").glob("2026_firestore_*.json")
            )
            self.assertEqual(len(upload_paths), 1)
            self.assertEqual(
                json.loads(upload_paths[0].read_text(encoding="utf-8")),
                firestore_payload,
            )


if __name__ == "__main__":
    unittest.main()
