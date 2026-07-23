import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.validation_feedback import (
    build_child_feedback,
    build_improvement_report,
    feedback_prompt,
    write_improvement_report,
)


def _feedback(child, *, attempt=1, question_id="q1", stage_id="explanation"):
    return build_child_feedback(
        child,
        attempt=attempt,
        question_id=question_id,
        stage_id=stage_id,
    )


class ChildFeedbackTests(unittest.TestCase):
    def test_validated_success_is_accepted(self):
        feedback = _feedback(
            {
                "runId": "child-1",
                "status": "succeeded",
                "receiptValidated": True,
                "result": {
                    "status": "succeeded",
                    "summary": "一問を確定しました。",
                    "commands": [{"command": "checker", "status": "pass"}],
                },
            }
        )

        self.assertEqual(feedback["status"], "accepted")
        self.assertEqual(feedback["issues"], [])
        self.assertFalse(feedback["modelPassServerReject"])
        self.assertEqual(feedback["resultSummary"], "一問を確定しました。")

    def test_source_binding_rejection_is_retryable_and_bounded(self):
        feedback = _feedback(
            {
                "runId": "child-2",
                "status": "failed",
                "error": (
                    "sourceQuestionKeyとsourceRecordRefのbindingが一致しません。 "
                    "token=super-secret " + "x" * 500
                ),
                "result": {"status": "failed", "commands": []},
            }
        )

        self.assertEqual(feedback["status"], "retryable")
        self.assertEqual(feedback["issues"][0]["code"], "source_binding")
        self.assertTrue(feedback["issues"][0]["retryable"])
        self.assertNotIn("super-secret", feedback["issues"][0]["message"])
        self.assertLessEqual(len(feedback["issues"][0]["message"]), 320)

    def test_safety_failures_are_not_retryable(self):
        cases = (
            ("既存00_sourceの不変条件に違反しました。", "source_immutability"),
            ("policyFingerprintが開始時と一致しません。", "policy_drift"),
            (
                "実行中にexplanationの作業版又は正本文書が変更されました。",
                "policy_drift",
            ),
            ("rollback後も残存差分があります。", "rollback_unsafe"),
        )
        for message, code in cases:
            with self.subTest(code=code):
                feedback = _feedback({"status": "failed", "error": message})
                self.assertEqual(feedback["status"], "blocked")
                self.assertEqual(feedback["issues"][0]["code"], code)
                self.assertFalse(feedback["issues"][0]["retryable"])

    def test_source_identical_originalized_body_is_retryable_content_feedback(self):
        feedback = _feedback(
            {
                "runId": "child-originalize",
                "status": "failed",
                "error": (
                    "05_originalizedの問題文全体が"
                    "00_sourceと完全一致しています。"
                ),
                "result": {
                    "status": "failed",
                    "summary": (
                        "05_originalizedの問題文全体が"
                        "00_sourceと完全一致しています。"
                    ),
                    "commands": [
                        {"command": "question content", "status": "fail"}
                    ],
                },
            },
            stage_id="originalize",
        )

        self.assertEqual(feedback["status"], "retryable")
        issue = next(
            issue
            for issue in feedback["issues"]
            if issue["code"] == "originalization_required"
        )
        self.assertEqual(issue["field"], "questionBodyText")
        self.assertTrue(issue["retryable"])
        self.assertNotIn(
            "source_immutability",
            {value["code"] for value in feedback["issues"]},
        )

    def test_write_safety_failures_are_not_retried(self):
        cases = (
            (
                "失敗turnで整備責務外のfile変更を検出しました。",
                "scope_violation",
                "writeScope",
            ),
            (
                "allowed scope外の対象外fileを変更しました。",
                "scope_violation",
                "writeScope",
            ),
            (
                "agent_outputにはresult.json以外を保存できません。",
                "scope_violation",
                "writeScope",
            ),
            (
                "整備差分にsymlinkは使用できません。",
                "symlink_violation",
                "filesystem.symlink",
            ),
            (
                "record baselineのhashが一致しません。",
                "transaction_integrity",
                "transaction",
            ),
            (
                "manifest安全境界に違反しました。",
                "transaction_integrity",
                "transaction",
            ),
            (
                "整備用writable rootがrepository外です。",
                "scope_violation",
                "writeScope",
            ),
        )
        for message, code, field in cases:
            with self.subTest(code=code, message=message):
                feedback = _feedback({"status": "failed", "error": message})
                issue = feedback["issues"][0]
                self.assertEqual(feedback["status"], "blocked")
                self.assertEqual(issue["code"], code)
                self.assertEqual(issue["field"], field)
                self.assertFalse(issue["retryable"])

    def test_receipt_error_preserves_safety_classification(self):
        feedback = _feedback(
            {
                "status": "failed",
                "receiptError": "完了receiptにsymlinkは使用できません。",
                "result": {"status": "failed", "commands": []},
            }
        )

        self.assertEqual(feedback["status"], "blocked")
        self.assertEqual(feedback["issues"][0]["code"], "symlink_violation")
        self.assertFalse(feedback["issues"][0]["retryable"])

    def test_record_scope_rejection_is_an_identity_issue(self):
        for message in (
            "public_question_idが対象recordと一致しません。",
            "review_question_idのrecord scopeが不正です。",
            "sourceQuestionKeyとsourceRecordRefの内容が一致しません。",
        ):
            with self.subTest(message=message):
                feedback = _feedback({"status": "failed", "error": message})
                self.assertIn(
                    feedback["issues"][0]["code"],
                    {"record_identity", "source_binding"},
                )
                self.assertTrue(feedback["issues"][0]["retryable"])

    def test_unsafe_rollback_blocks_retry_even_with_retryable_issue(self):
        feedback = _feedback(
            {
                "status": "failed",
                "receiptError": "完了receiptのschemaが不正です。",
                "rollback": {
                    "status": "failed",
                    "deltaUnknown": True,
                    "remainingChangedFiles": ["output/patch.json"],
                },
                "result": {"status": "failed", "commands": []},
            }
        )

        self.assertEqual(feedback["status"], "blocked")
        self.assertEqual(
            {issue["code"] for issue in feedback["issues"]},
            {"receipt_validation", "rollback_unsafe"},
        )
        self.assertFalse(feedback["issues"][-1]["retryable"])

    def test_failed_command_becomes_machine_validation_without_command_output(self):
        feedback = _feedback(
            {
                "status": "failed",
                "result": {
                    "status": "failed",
                    "commands": [
                        {
                            "command": (
                                "quality-gate --token secret-value " + "x" * 500
                            ),
                            "status": "fail",
                            "exitCode": 1,
                            "output": "large and secret output",
                        }
                    ],
                },
            }
        )

        issue = next(
            issue
            for issue in feedback["issues"]
            if issue["code"] == "machine_validation"
        )
        self.assertEqual(issue["field"], "result.commands")
        self.assertNotIn("secret-value", json.dumps(feedback, ensure_ascii=False))
        self.assertNotIn("large and secret", json.dumps(feedback, ensure_ascii=False))
        self.assertEqual(len(feedback["failedChecks"]), 1)
        self.assertEqual(feedback["failedChecks"][0]["exitCode"], 1)
        self.assertLessEqual(len(feedback["failedChecks"][0]["command"]), 320)

    def test_feedback_has_bounded_failure_context(self):
        feedback = _feedback(
            {
                "status": "failed",
                "error": "reason " + "r" * 500,
                "receiptError": "receipt " + "e" * 500,
                "result": {
                    "status": "failed",
                    "summary": "summary " + "s" * 500,
                    "commands": [
                        {"command": f"check-{index}", "status": "fail"}
                        for index in range(7)
                    ],
                },
            }
        )

        self.assertLessEqual(len(feedback["reason"]), 320)
        self.assertLessEqual(len(feedback["receiptError"]), 320)
        self.assertLessEqual(len(feedback["resultSummary"]), 320)
        self.assertEqual(len(feedback["failedChecks"]), 5)

    def test_all_model_commands_pass_but_server_rejects_is_recorded(self):
        feedback = _feedback(
            {
                "status": "failed",
                "error": "サーバー検証に失敗しました。",
                "result": {
                    "status": "failed",
                    "commands": [{"command": "checker", "status": "pass"}],
                },
            }
        )

        self.assertTrue(feedback["modelPassServerReject"])

    def test_server_commit_failure_is_non_retryable(self):
        feedback = _feedback(
            {
                "status": "failed",
                "error": "派生sourceUniqueKeysを再現できません。",
                "result": {
                    "status": "failed",
                    "commands": [
                        {"command": "question content", "status": "pass"},
                        {"command": "server commit", "status": "fail"},
                    ],
                },
                "rollback": {
                    "status": "succeeded",
                    "remainingChangedFiles": [],
                    "deltaUnknown": False,
                },
            }
        )

        self.assertEqual(feedback["status"], "blocked")
        self.assertIn(
            "server_validation",
            [issue["code"] for issue in feedback["issues"]],
        )

    def test_aggregate_review_hold_is_non_retryable(self):
        feedback = _feedback(
            {
                "status": "failed",
                "error": "集約回答レビューを保留しました。",
                "result": {"status": "failed", "commands": []},
            }
        )

        self.assertEqual(feedback["status"], "blocked")

    def test_aggregate_review_checkpoint_integrity_is_non_retryable(self):
        for message in (
            "aggregate review execution evidenceが予約契約と一致しません。",
            "aggregate review checkpointに未知のslotがあります。",
            "aggregate review slotの形式が不正です。",
            "aggregate review slotの番号又は状態が不正です。",
            "確定済みaggregate review slotの証拠が不正です。",
            "aggregate review slotとlegacy配列が一致しません。",
            "aggregate review checkpoint slotsの形式が不正です。",
            "legacy aggregate review checkpointの形式が不正です。",
            "legacy aggregate review executionの順序が不正です。",
            "aggregate review slot予約を再読検証できません。",
            "aggregate review予約取消を再読検証できません。",
            "aggregate review予約を原子的に取消できません。",
            "aggregate review checkpoint signatureが一致しません。",
            "開始済みaggregate review slotを確認できません。",
            "aggregate review slot確定を再読検証できません。",
            "aggregate review consensus signatureが一致しません。",
            "二つのaggregate review slot確定前にconsensusを保存できません。",
            "aggregate review consensusを再読検証できません。",
        ):
            with self.subTest(message=message):
                feedback = _feedback(
                    {
                        "status": "failed",
                        "error": message,
                        "result": {"status": "failed", "commands": []},
                    }
                )

                self.assertEqual(feedback["status"], "blocked")
                self.assertEqual(
                    feedback["issues"][0]["code"],
                    "aggregate_review_checkpoint_integrity",
                )
                self.assertFalse(feedback["issues"][0]["retryable"])

    def test_missing_stable_parent_identity_is_non_retryable(self):
        feedback = _feedback(
            {
                "status": "failed",
                "error": "stable source identity is required for derived statement IDs",
                "result": {"status": "failed", "commands": []},
            }
        )

        self.assertEqual(feedback["status"], "blocked")
        self.assertEqual(
            feedback["issues"][0]["code"],
            "stable_parent_identity",
        )
        self.assertFalse(feedback["issues"][0]["retryable"])

    def test_feedback_prompt_contains_only_bounded_correction_contract(self):
        feedback = _feedback(
            {"status": "failed", "error": "record identityが一致しません。"},
            attempt=2,
        )

        prompt = feedback_prompt(feedback)

        self.assertIn("この一問・工程だけ", prompt)
        self.assertIn("00_sourceは変更しない", prompt)
        self.assertIn('"attempt": 2', prompt)
        self.assertNotIn("childRunId", prompt)

    def test_feedback_prompt_redacts_untrusted_issue_message(self):
        prompt = feedback_prompt(
            {
                "status": "retryable",
                "issues": [
                    {
                        "code": "machine_validation",
                        "field": "result.commands",
                        "message": "authorization: Bearer sk-secretvalue123",
                        "retryable": True,
                    }
                ],
            }
        )

        self.assertNotIn("sk-secretvalue123", prompt)
        self.assertIn("[REDACTED]", prompt)

    def test_feedback_prompt_includes_sanitized_failure_context(self):
        feedback = _feedback(
            {
                "status": "failed",
                "error": "record identityが一致しません。",
                "result": {
                    "status": "failed",
                    "summary": "対象recordを再確認してください。",
                    "commands": [
                        {
                            "command": "checker --api_key=secret-value",
                            "status": "fail",
                        }
                    ],
                },
            }
        )

        prompt = feedback_prompt(feedback)

        self.assertIn('"failedChecks":', prompt)
        self.assertIn('"resultSummary":', prompt)
        self.assertNotIn("secret-value", prompt)


class ImprovementReportTests(unittest.TestCase):
    def test_groups_by_stage_code_and_field_and_marks_repeated_candidate(self):
        questions = [
            {
                "questionId": f"q{index}",
                "stages": [
                    {
                        "stageId": "explanation",
                        "validationAttempts": [
                            {
                                "issues": [
                                    {
                                        "code": "machine_validation",
                                        "field": "explanationText",
                                    }
                                ]
                            },
                            {
                                "issues": [
                                    {
                                        "code": "machine_validation",
                                        "field": "explanationText",
                                    }
                                ]
                            },
                        ]
                        if index == 1
                        else [
                            {
                                "feedback": {
                                    "issues": [
                                        {
                                            "code": "machine_validation",
                                            "field": "explanationText",
                                        }
                                    ]
                                }
                            }
                        ],
                    }
                ],
            }
            for index in range(1, 4)
        ]

        report = build_improvement_report(questions)

        self.assertEqual(report["distinctQuestionCount"], 3)
        self.assertEqual(report["attemptCount"], 4)
        self.assertEqual(
            report["findings"],
            [
                {
                    "stageId": "explanation",
                    "code": "machine_validation",
                    "field": "explanationText",
                    "distinctQuestionCount": 3,
                    "attemptCount": 4,
                    "modelPassServerReject": False,
                    "candidate": True,
                }
            ],
        )

    def test_model_pass_server_reject_is_candidate_for_one_question(self):
        report = build_improvement_report(
            [
                {
                    "questionId": "q1",
                    "stages": [
                        {
                            "stageId": "law_audit",
                            "validationAttempts": [
                                {
                                    "modelPassServerReject": True,
                                    "issues": [
                                        {
                                            "code": "source_binding",
                                            "field": "record.sourceBinding",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        )

        self.assertTrue(report["findings"][0]["candidate"])
        self.assertTrue(report["findings"][0]["modelPassServerReject"])

    def test_no_logs_returns_empty_report(self):
        self.assertEqual(
            build_improvement_report([{"questionId": "q1", "stages": []}]),
            {
                "schemaVersion": "question-maintenance-improvement-report/v1",
                "distinctQuestionCount": 0,
                "attemptCount": 0,
                "findings": [],
            },
        )

    def test_report_is_written_atomically_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            report = build_improvement_report([])

            target = write_improvement_report(run_dir, report)

            self.assertEqual(json.loads(target.read_text()), report)
            self.assertEqual(list(run_dir.glob("*.tmp")), [])

            target.unlink()
            destination = root / "outside.json"
            destination.write_text("keep", encoding="utf-8")
            target.symlink_to(destination)
            with self.assertRaisesRegex(ValueError, "symlink"):
                write_improvement_report(run_dir, report)
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep")

    def test_symlinked_run_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlink"):
                write_improvement_report(linked, build_improvement_report([]))


if __name__ == "__main__":
    unittest.main()
