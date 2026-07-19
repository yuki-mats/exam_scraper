import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.inventory import QuestionInventory, detect_issues
from tools.question_review_console.patch_validation import (
    law_audit_quality_warnings,
    patch_entry_required_warnings,
    upload_document_required_warnings,
)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class QuestionReviewInventoryTests(unittest.TestCase):
    def test_invalid_source_record_fails_closed_instead_of_reducing_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
                / "question.json"
            )
            write_json(source, {"question_bodies": ["not-an-object"]})

            with self.assertRaisesRegex(ValueError, "source record must be an object"):
                QuestionInventory(root).group("sample-exam", "2026")

    def test_source_selection_matches_physical_merge_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            write_json(
                source_dir / "question.json",
                {
                    "question_bodies": [
                        {"original_question_id": "q1", "questionBodyText": "正本"}
                    ]
                },
            )
            write_json(
                source_dir / "question_merged.json",
                {"question_bodies": ["物理Merge入力ではない"]},
            )
            write_json(
                source_dir / "question_questionType_fixed.json",
                ["patch名のfileはsourceではない"],
            )

            group = QuestionInventory(root).group("sample-exam", "2026")

        self.assertEqual(len(group["questions"]), 1)
        self.assertEqual(group["questions"][0]["sourceRecordRef"], "question.json#0")

    def test_projected_input_reads_one_record_from_source_and_current_patches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            source = {
                "original_question_id": "q1",
                "questionBodyText": "問題",
                "choiceTextList": ["A"],
                "questionType": "multiple_choice",
            }
            write_json(
                group / "00_source" / "question.json",
                {"question_bodies": [source]},
            )
            write_json(
                group / "10_questionType_fixed" / "question_questionType_fixed.json",
                [
                    {
                        "original_question_id": "q1",
                        "questionType": "flash_card",
                    }
                ],
            )

            result = QuestionInventory(root).projected_input(
                "sample-exam",
                "2026",
                "question.json#0",
            )

        self.assertEqual(result.record["questionType"], "flash_card")
        self.assertEqual(result.errors, ())
        self.assertEqual(len(result.applied_files), 1)

    def test_projected_input_rejects_an_unmatched_patch_like_physical_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            write_json(
                group / "00_source" / "question.json",
                {
                    "question_bodies": [
                        {"original_question_id": "q1", "questionBodyText": "問題"}
                    ]
                },
            )
            write_json(
                group / "10_questionType_fixed" / "orphan_questionType_fixed.json",
                [
                    {
                        "original_question_id": "orphan",
                        "questionType": "flash_card",
                    }
                ],
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "source recordへ対応できないquestionType patch",
            ):
                QuestionInventory(root).projected_input(
                    "sample-exam",
                    "2026",
                    "question.json#0",
                )

    def test_invalidate_clears_projection_caches_with_an_unchanged_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            write_json(
                group / "00_source" / "question.json",
                {
                    "question_bodies": [
                        {"original_question_id": "q1", "questionBodyText": "問題"}
                    ]
                },
            )
            patch_path = (
                group
                / "10_questionType_fixed"
                / "question_questionType_fixed.json"
            )
            write_json(
                patch_path,
                [{"original_question_id": "q1", "questionType": "flash_card"}],
            )
            original_stat = patch_path.stat()
            inventory = QuestionInventory(root)
            self.assertEqual(
                inventory.projected_input(
                    "sample-exam", "2026", "question.json#0"
                ).record["questionType"],
                "flash_card",
            )
            cache_key = ("sample-exam", "2026")
            self.assertIn(cache_key, inventory._source_cache)
            self.assertIn(cache_key, inventory._issue_index_cache)
            self.assertTrue(
                any(key[:2] == cache_key for key in inventory._stage_index_cache)
            )

            write_json(
                patch_path,
                [{"original_question_id": "q1", "questionType": "true_false"}],
            )
            self.assertEqual(patch_path.stat().st_size, original_stat.st_size)
            os.utime(
                patch_path,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            inventory.invalidate("sample-exam", "2026")
            self.assertNotIn(cache_key, inventory._source_cache)
            self.assertNotIn(cache_key, inventory._issue_index_cache)
            self.assertFalse(
                any(key[:2] == cache_key for key in inventory._stage_index_cache)
            )

            self.assertEqual(
                inventory.projected_input(
                    "sample-exam", "2026", "question.json#0"
                ).record["questionType"],
                "true_false",
            )

    def test_projected_input_reuses_source_and_unchanged_patch_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            write_json(
                group / "00_source" / "questions.json",
                {
                    "question_bodies": [
                        {"original_question_id": "q1", "questionBodyText": "一"},
                        {"original_question_id": "q2", "questionBodyText": "二"},
                    ]
                },
            )
            from tools.question_review_console import inventory as inventory_module

            with (
                patch.object(
                    inventory_module,
                    "load_source_record_inventory",
                    wraps=inventory_module.load_source_record_inventory,
                ) as source_mock,
                patch.object(
                    inventory_module,
                    "build_stage_map",
                    wraps=inventory_module.build_stage_map,
                ) as stage_mock,
                patch.object(
                    inventory_module,
                    "build_question_issue_index",
                    wraps=inventory_module.build_question_issue_index,
                ) as issue_mock,
            ):
                inventory = QuestionInventory(root)
                inventory.projected_input("sample-exam", "2026", "questions.json#0")
                inventory.projected_input("sample-exam", "2026", "questions.json#1")

                self.assertEqual(source_mock.call_count, 1)
                self.assertEqual(stage_mock.call_count, 7)
                self.assertEqual(issue_mock.call_count, 1)

                write_json(
                    group
                    / "10_questionType_fixed"
                    / "questions_questionType_fixed.json",
                    [{"original_question_id": "q1", "questionType": "flash_card"}],
                )
                inventory.projected_input("sample-exam", "2026", "questions.json#0")

                write_json(
                    group / "00_source" / "question_3.json",
                    {
                        "question_bodies": [
                            {"original_question_id": "q3", "questionBodyText": "三"}
                        ]
                    },
                )
                inventory.projected_input(
                    "sample-exam", "2026", "question_3.json#0"
                )

            self.assertEqual(source_mock.call_count, 2)
            self.assertEqual(
                sum(
                    call.kwargs["stage"] == "questionType"
                    for call in stage_mock.call_args_list
                ),
                3,
            )
            self.assertEqual(stage_mock.call_count, 15)
            self.assertEqual(issue_mock.call_count, 2)

    def test_inventory_exposes_japanese_qualification_name_and_publication_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_json(
                root / "config" / "scrape_presets.json",
                {
                    "readable-code": {
                        "qualification_name": "表示用資格名",
                        "publication_qualification_id": "stable-public-id",
                    }
                },
            )
            source_dir = (
                root
                / "output"
                / "readable-code"
                / "questions_json"
                / "202501"
                / "00_source"
            )
            write_json(source_dir / "question_source_1.json", {"question_bodies": []})

            overview = QuestionInventory(root).inventory()

        self.assertEqual(
            overview["qualifications"],
            [
                {
                    "id": "readable-code",
                    "displayName": "表示用資格名",
                    "publicationId": "stable-public-id",
                    "listGroupIds": ["202501"],
                    "listGroupCount": 1,
                }
            ],
        )

    def test_reports_missing_top_level_upload_ready_verdict(self):
        warnings = upload_document_required_warnings(
            {
                "questionId": "doc1",
                "originalQuestionBodyText": "問題文",
                "questionSetId": "set1",
                "questionText": "問題文 [quote]選択肢[/quote]",
                "questionType": "true_false",
                "qualificationId": "sample",
                "explanationText": "間違い。根拠。",
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": True,
                "questionTags": [],
                "originalQuestionChoiceText": "選択肢",
                "isLawRelated": True,
                "lawReferences": [{"lawId": "law1"}],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "current": {"lawId": "law1"},
                    "evidenceSummary": {"verdict": "incorrect"},
                },
            }
        )

        self.assertEqual(
            [warning["field"] for warning in warnings],
            ["correctChoiceText"],
        )
        self.assertTrue(all(warning["documentId"] == "doc1" for warning in warnings))

    def test_allows_missing_law_snapshot_verdict_when_top_level_verdict_exists(self):
        document = {
                "questionId": "doc1",
                "originalQuestionBodyText": "問題文",
                "questionSetId": "set1",
                "questionText": "問題文 [quote]選択肢[/quote]",
                "questionType": "true_false",
                "qualificationId": "sample",
                "correctChoiceText": "間違い",
                "explanationText": "間違い。根拠。",
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": True,
                "questionTags": [],
                "originalQuestionChoiceText": "選択肢",
                "isLawRelated": True,
                "lawReferences": [{"lawId": "law1"}],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "current": {"lawId": "law1"},
                    "evidenceSummary": {"verdict": "incorrect"},
                },
            }

        warnings = upload_document_required_warnings(document)

        self.assertEqual(warnings, [])

        quality_warnings = law_audit_quality_warnings(document)
        self.assertEqual(len(quality_warnings), 1)
        self.assertEqual(
            quality_warnings[0]["code"], "law_audit_metadata_incomplete"
        )
        self.assertEqual(
            quality_warnings[0]["field"],
            "lawRevisionFacts.current.correctChoiceText",
        )
        self.assertFalse(quality_warnings[0]["blocksSync"])
        self.assertTrue(quality_warnings[0]["blocksPublish"])

    def test_reports_law_snapshot_verdict_mismatch_separately(self):
        warnings = law_audit_quality_warnings(
            {
                "questionId": "doc1",
                "correctChoiceText": "正しい",
                "isLawRelated": True,
                "lawReferences": [{"lawId": "law1"}],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "current": {"correctChoiceText": "間違い"},
                    "evidenceSummary": {"verdict": "correct"},
                },
            }
        )

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "law_audit_verdict_mismatch")

    def test_detects_missing_required_fields_in_applied_patch_entry(self):
        warnings = patch_entry_required_warnings(
            {
                "original_question_id": "q1",
                "explanationText": ["正しい。根拠"],
            },
            "explanation",
        )

        self.assertEqual(
            {warning["field"] for warning in warnings},
            {"question_url", "suggestedQuestions", "suggestedQuestionDetails"},
        )

    def test_correct_choice_patch_does_not_require_question_url(self):
        warnings = patch_entry_required_warnings(
            {
                "original_question_id": "q1",
                "correctChoiceText": ["正しい"],
            },
            "correctChoice",
        )

        self.assertEqual(warnings, [])

    def test_reports_all_projected_required_field_warnings(self):
        projected = {
            "questionBodyText": "",
            "choiceTextList": ["A", ""],
            "correctChoiceText": ["未確定"],
            "explanationText": [""],
        }

        issues = detect_issues(projected, projected, [], [], [])
        required = next(
            issue for issue in issues if issue["code"] == "required_field_missing"
        )

        self.assertEqual(
            required["fields"],
            [
                "choiceTextList",
                "correctChoiceText",
                "explanationText",
                "questionBodyText",
                "questionType",
            ],
        )
        self.assertIn("問題文がありません。", required["detail"])
        self.assertIn("正誤数が選択肢数と一致しません。", required["detail"])

    def test_malformed_choice_arrays_do_not_stop_converted_comparison(self):
        projected = {
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["正しい"],
            "explanationText": ["正しい。根拠", "間違い。根拠"],
            "questionType": "multiple_choice",
            "isLawRelated": False,
        }
        converted = [
            {
                "questionId": f"doc{index}",
                "originalQuestionChoiceText": choice,
                "correctChoiceText": verdict,
                "explanationText": explanation,
            }
            for index, (choice, verdict, explanation) in enumerate(
                [
                    ("A", "正しい", "正しい。根拠"),
                    ("B", "間違い", "間違い。根拠"),
                ]
            )
        ]

        issues = detect_issues(projected, projected, converted, converted, [])

        required = next(
            issue for issue in issues if issue["code"] == "required_field_missing"
        )
        self.assertIn("correctChoiceText", required["fields"])
        self.assertNotIn("convert_stale", {issue["code"] for issue in issues})

    def test_merge_comparison_treats_verdict_synonyms_as_equal(self):
        projected = {
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["正解", "不正解"],
            "explanationText": ["正解。", "不正解。"],
            "questionType": "multiple_choice",
            "isLawRelated": False,
        }
        merged = {**projected, "correctChoiceText": ["正しい", "間違い"]}

        issues = detect_issues(projected, merged, [], [], [])

        self.assertNotIn("merge_stale", {issue["code"] for issue in issues})

    def test_discovers_qualification_and_projects_latest_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            source = {
                "original_question_id": "q1",
                "questionLabel": "問1",
                "questionBodyText": "正しいものはどれか。",
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
                "explanationText": ["正しい。旧", "間違い。旧"],
                "questionType": "multiple_choice",
                "isLawRelated": False,
            }
            write_json(group / "00_source" / "question_2026_1.json", {"question_bodies": [source]})
            write_json(
                group / "30_merged_2" / "question_2026_1_merged.json",
                {"question_bodies": [{**source, "questionType": "stale_type"}]},
            )
            write_json(
                group / "21_explanationText_added" / "question_2026_1_explanationText_added.json",
                [
                    {
                        "original_question_id": "q1",
                        "question_url": "https://example.test/q1",
                        "explanationText": ["正しい。新", "間違い。新"],
                        "suggestedQuestions": [],
                        "suggestedQuestionDetails": [],
                    }
                ],
            )
            firestore_docs = [
                {
                    "questionId": f"doc{index}",
                    "qualificationId": "sample-exam",
                    "originalQuestionId": "q1",
                    "originalQuestionBodyText": source["questionBodyText"],
                    "originalQuestionChoiceText": choice,
                    "correctChoiceText": source["correctChoiceText"][index],
                    "explanationText": ["正しい。新", "間違い。新"][index],
                }
                for index, choice in enumerate(source["choiceTextList"])
            ]
            write_json(
                group / "40_convert" / "question_2026_firestore_20260712_120000.json",
                {"questions": firestore_docs},
            )

            inventory = QuestionInventory(root)
            overview = inventory.inventory()
            payload = inventory.group("sample-exam", "2026")

        self.assertEqual(overview["qualifications"][0]["id"], "sample-exam")
        self.assertEqual(payload["questionCount"], 1)
        question = payload["questions"][0]
        self.assertEqual(question["originalQuestionId"], "q1")
        self.assertEqual(question["projected"]["questionType"], "multiple_choice")
        self.assertEqual(question["projected"]["explanationText"], ["正しい。新", "間違い。新"])
        self.assertIn("merge_stale", question["issueCodes"])
        self.assertNotIn("convert_stale", question["issueCodes"])

    def test_source_key_is_derived_from_source_not_patch_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            source = {
                "original_question_id": "q1",
                "questionLabel": "問1",
                "questionBodyText": "問題文",
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
            }
            write_json(
                group / "00_source" / "question_2026_1.json",
                {"question_bodies": [source]},
            )
            write_json(
                group / "10_questionType_fixed" / "question_2026_1_questionType_fixed.json",
                [
                    {
                        "original_question_id": "q1",
                        "sourceQuestionKey": "patch:injected:key",
                        "questionType": "true_false",
                    }
                ],
            )

            question = QuestionInventory(root).group(
                "sample-exam",
                "2026",
            )["questions"][0]

        self.assertEqual(
            question["sourceQuestionKey"],
            "sample-exam:2026:q1",
        )
        self.assertEqual(
            question["sourceRecordRef"],
            "question_2026_1.json#0",
        )

    def test_duplicate_two_field_identity_is_disambiguated_by_source_record_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            duplicate = {
                "original_question_id": "q1",
                "sourceQuestionKey": "sample:2026:q1",
                "questionBodyText": "問題文",
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
            }
            write_json(
                source_dir / "question_2026_1.json",
                {"question_bodies": [duplicate]},
            )
            write_json(
                source_dir / "question_2026_2.json",
                {"question_bodies": [duplicate]},
            )
            write_json(
                source_dir.parent
                / "10_questionType_fixed"
                / "shared_questionType_fixed.json",
                [
                    {
                        "original_question_id": "q1",
                        "sourceQuestionKey": "sample:2026:q1",
                        "sourceRecordRef": source_ref,
                        "questionType": question_type,
                    }
                    for source_ref, question_type in (
                        ("question_2026_1.json#0", "true_false"),
                        ("question_2026_2.json#0", "flash_card"),
                    )
                ],
            )

            group = QuestionInventory(root).group("sample-exam", "2026")

        self.assertEqual(group["questionCount"], 2)
        self.assertEqual(group["identityBlockers"], [])
        self.assertEqual(
            {
                question["sourceRecordRef"]
                for question in group["questions"]
            },
            {"question_2026_1.json#0", "question_2026_2.json#0"},
        )
        self.assertEqual(len({q["id"] for q in group["questions"]}), 2)
        self.assertEqual(
            {
                question["sourceRecordRef"]: question["projected"]["questionType"]
                for question in group["questions"]
            },
            {
                "question_2026_1.json#0": "true_false",
                "question_2026_2.json#0": "flash_card",
            },
        )

    def test_group_reports_unmatched_stage_and_issue_correction_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            write_json(
                group / "00_source" / "question_2026_1.json",
                {
                    "question_bodies": [
                        {
                            "original_question_id": "q1",
                            "questionBodyText": "問題文",
                            "choiceTextList": ["A"],
                            "correctChoiceText": ["正しい"],
                        }
                    ]
                },
            )
            write_json(
                group
                / "10_questionType_fixed"
                / "question_2026_1_questionType_fixed.json",
                [
                    {
                        "original_question_id": "orphan-stage",
                        "questionType": "true_false",
                    }
                ],
            )
            write_json(
                group / "24_questionIssueCorrections" / "orphan.json",
                {
                    "schemaVersion": "question-issue-correction/v1",
                    "origin": "user_problem_report",
                    "entries": [
                        {
                            "original_question_id": "orphan-issue",
                            "expectedBeforeHash": "0" * 64,
                            "changes": {"questionBodyText": "修正"},
                        }
                    ],
                },
            )

            payload = QuestionInventory(root).group("sample-exam", "2026")

        blockers = payload["artifactResolutionBlockers"]
        self.assertEqual(
            {(blocker["patchDir"], blocker["count"]) for blocker in blockers},
            {
                ("10_questionType_fixed", 1),
                ("24_questionIssueCorrections", 1),
            },
        )
        self.assertTrue(
            all(blocker["code"] == "artifact_identity_unmatched" for blocker in blockers)
        )
        self.assertIn("question_2026_1_questionType_fixed.json", blockers[0]["path"])

    def test_group_reports_same_artifact_binding_conflict_as_a_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            write_json(
                group / "00_source" / "question_2026_1.json",
                {
                    "question_bodies": [
                        {
                            "sourceQuestionKey": "sample-exam:2026:q1",
                            "original_question_id": "q1",
                            "questionBodyText": "問題文",
                            "choiceTextList": ["A"],
                            "correctChoiceText": ["正しい"],
                        }
                    ]
                },
            )
            binding = {
                "sourceQuestionKey": "sample-exam:2026:q1",
                "reviewQuestionId": "q1",
                "sourceRecordRef": "question_2026_1.json#0",
            }
            write_json(
                group
                / "10_questionType_fixed"
                / "question_2026_1_questionType_fixed.json",
                [
                    {**binding, "questionType": "true_false"},
                    {**binding, "questionType": "flash_card"},
                ],
            )

            payload = QuestionInventory(root).group("sample-exam", "2026")

        conflicts = [
            blocker
            for blocker in payload["artifactResolutionBlockers"]
            if blocker["code"] == "artifact_identity_conflict"
            and blocker["patchDir"] == "10_questionType_fixed"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("同一artifact内", conflicts[0]["message"])
        self.assertTrue(
            any(
                issue["code"] == "projection_error"
                for issue in payload["questions"][0]["issues"]
            )
        )


if __name__ == "__main__":
    unittest.main()
