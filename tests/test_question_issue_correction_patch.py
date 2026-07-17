from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.check.check_question_issue_correction_patch import validate_patch
from scripts.common.question_identity import SourceIdentityBinding, SourceRecordIdentity
from scripts.merge.question_issue_corrections import (
    apply_question_issue_correction_index,
    apply_question_issue_correction_patch,
    apply_question_issue_correction_paths,
    build_question_issue_correction_index,
    ensure_all_question_issue_corrections_applied,
    question_record_hash,
    sha256_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config/question_issue_reports.json"


def current_record() -> dict:
    return {
        "original_question_id": "question-1",
        "public_question_id": "question-1",
        "question_url": "https://official.example/question-1",
        "list_group_id": "2026",
        "questionBodyText": "修正前",
        "choiceTextList": ["A", "B"],
        "questionType": "true_false",
        "questionIntent": "select_correct",
        "correctChoiceText": ["正しい", "間違い"],
    }


def valid_patch(record: dict) -> dict:
    evidence_hash = sha256_json("official evidence")
    return {
        "schemaVersion": "question-issue-correction/v1",
        "origin": "user_problem_report",
        "batchId": "qir-20260710-example",
        "category": "question_content",
        "caseIds": ["case-1"],
        "inputCaseHashes": {"case-1": "a" * 64},
        "reviewProtocol": "blind-a-b-challenge/v1",
        "blindReviewHashes": ["b" * 64, "c" * 64],
        "challengeReviewHash": "d" * 64,
        "createdAt": "2026-07-10T00:00:00Z",
        "entries": [
            {
                "original_question_id": "question-1",
                "expectedBeforeHash": question_record_hash(record),
                "changes": {"questionBodyText": "修正後"},
                "rationale": "公式問題冊子と一致させる",
                "evidence": [
                    {
                        "sourceClass": "official",
                        "locator": "official-document-1",
                        "title": "公式問題冊子",
                        "verifiedAt": "2026-07-10T00:00:00Z",
                        "contentHash": evidence_hash,
                    }
                ],
            }
        ],
    }


class QuestionIssueCorrectionPatchTests(unittest.TestCase):
    def test_exact_binding_is_valid_and_updates_only_its_source_record(self) -> None:
        record = current_record()
        first = SourceIdentityBinding.from_values(
            "sample:2026:question-1",
            "question-1",
            "question_1.json#0",
        )
        second = SourceIdentityBinding.from_values(
            "sample:2026:question-1",
            "question-1",
            "question_2.json#0",
        )
        patch = valid_patch(record)
        patch["entries"][0].update(second.as_mapping())
        sources = [
            SourceRecordIdentity(
                binding=binding,
                aliases=frozenset(binding.as_tuple()),
                source_stem=source_stem,
            )
            for binding, source_stem in (
                (first, "question_1"),
                (second, "question_2"),
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            patch_path = root / "patch.json"
            current_path = root / "current.json"
            patch_path.write_text(json.dumps(patch), encoding="utf-8")
            current_path.write_text(
                json.dumps({"question_bodies": [record]}),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_patch(
                    patch_path,
                    config_path=CONFIG_PATH,
                    current_path=current_path,
                ),
                [],
            )
            index = build_question_issue_correction_index(
                [patch_path],
                sources,
            )
            with self.assertRaisesRegex(ValueError, "source inventory index"):
                apply_question_issue_correction_patch(
                    {"question_bodies": [copy.deepcopy(record)]},
                    patch_path,
                )
            data = {
                "question_bodies": [
                    copy.deepcopy(record),
                    copy.deepcopy(record),
                ]
            }
            updates = apply_question_issue_correction_index(
                data,
                index,
                [first, second],
            )

        self.assertEqual(updates, 1)
        self.assertEqual(data["question_bodies"][0]["questionBodyText"], "修正前")
        self.assertEqual(data["question_bodies"][1]["questionBodyText"], "修正後")

    def test_valid_patch_applies_without_copying_provenance_into_question(self) -> None:
        record = current_record()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            patch_path = root / "patch.json"
            current_path = root / "current.json"
            patch_path.write_text(
                json.dumps(valid_patch(record), ensure_ascii=False),
                encoding="utf-8",
            )
            current_path.write_text(
                json.dumps({"question_bodies": [record]}, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_patch(
                    patch_path,
                    config_path=CONFIG_PATH,
                    current_path=current_path,
                ),
                [],
            )
            data = {"question_bodies": [copy.deepcopy(record)]}
            self.assertEqual(apply_question_issue_correction_patch(data, patch_path), 1)
            self.assertEqual(data["question_bodies"][0]["questionBodyText"], "修正後")
            self.assertNotIn("caseIds", data["question_bodies"][0])
            self.assertNotIn("reportProvenance", data["question_bodies"][0])

    def test_stale_input_hash_stops_patch_application(self) -> None:
        record = current_record()
        patch = valid_patch(record)
        patch["entries"][0]["expectedBeforeHash"] = "0" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            patch_path = Path(temp_dir) / "patch.json"
            patch_path.write_text(json.dumps(patch), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "input hash mismatch"):
                apply_question_issue_correction_patch(
                    {"question_bodies": [copy.deepcopy(record)]},
                    patch_path,
                )

    def test_patch_rejects_raw_comment_and_category_escape(self) -> None:
        record = current_record()
        patch = valid_patch(record)
        patch["detailComment"] = "raw user comment"
        patch["entries"][0]["changes"]["correctChoiceText"] = [
            "間違い",
            "正しい",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            patch_path = root / "patch.json"
            current_path = root / "current.json"
            patch_path.write_text(json.dumps(patch), encoding="utf-8")
            current_path.write_text(
                json.dumps({"question_bodies": [record]}),
                encoding="utf-8",
            )

            errors = validate_patch(
                patch_path,
                config_path=CONFIG_PATH,
                current_path=current_path,
            )
            self.assertTrue(any("private report fields" in error for error in errors))
            self.assertTrue(any("not allowed" in error for error in errors))

    def test_patch_rejects_fields_that_do_not_change_current_record(self) -> None:
        record = current_record()
        patch = valid_patch(record)
        patch["entries"][0]["changes"] = {
            "questionBodyText": record["questionBodyText"]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            patch_path = root / "patch.json"
            current_path = root / "current.json"
            patch_path.write_text(json.dumps(patch), encoding="utf-8")
            current_path.write_text(
                json.dumps({"question_bodies": [record]}),
                encoding="utf-8",
            )

            errors = validate_patch(
                patch_path,
                config_path=CONFIG_PATH,
                current_path=current_path,
            )

            self.assertTrue(
                any("changes must differ from current values" in error for error in errors)
            )

    def test_validation_rejects_duplicate_current_alias_instead_of_last_write(self) -> None:
        record = current_record()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            patch_path = root / "patch.json"
            current_path = root / "current.json"
            patch_path.write_text(json.dumps(valid_patch(record)), encoding="utf-8")
            current_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            record,
                            {**record, "questionBodyText": "別レコード"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            errors = validate_patch(
                patch_path,
                config_path=CONFIG_PATH,
                current_path=current_path,
            )

        self.assertTrue(
            any("does not resolve uniquely" in error for error in errors),
            errors,
        )

    def test_merge_applies_chained_overlays_and_checks_all_targets(self) -> None:
        record = current_record()
        first = valid_patch(record)
        after_first = copy.deepcopy(record)
        after_first["questionBodyText"] = "修正後"
        second = valid_patch(after_first)
        second["batchId"] = "qir-20260711-example"
        second["entries"][0]["changes"] = {
            "choiceTextList": ["選択肢A", "選択肢B"]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_path = root / "01_first.json"
            second_path = root / "02_second.json"
            first_path.write_text(json.dumps(first), encoding="utf-8")
            second_path.write_text(json.dumps(second), encoding="utf-8")
            data = {"question_bodies": [copy.deepcopy(record)]}
            applied: set[str] = set()

            self.assertEqual(
                apply_question_issue_correction_paths(
                    data,
                    [first_path, second_path],
                    applied_targets=applied,
                ),
                2,
            )
            ensure_all_question_issue_corrections_applied(
                [first_path, second_path],
                applied,
            )
            self.assertEqual(data["question_bodies"][0]["questionBodyText"], "修正後")
            self.assertEqual(
                data["question_bodies"][0]["choiceTextList"],
                ["選択肢A", "選択肢B"],
            )


if __name__ == "__main__":
    unittest.main()
