import json
import unittest

from tools.question_review_console.question_candidate import (
    SCHEMA_VERSION,
    QuestionCandidateError,
    candidate_targets,
    output_schema,
    parse_candidates,
    validate_candidate_content,
)


class QuestionCandidateTest(unittest.TestCase):
    def plan(self):
        return {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/21_explanationText_added/patch.json"
            ],
            "allowedWriteFiles": [],
        }

    def test_builds_targets_without_exposing_unrelated_paths(self):
        targets = candidate_targets("q1", "explanation", self.plan())

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].target_id, "q1:explanation")
        self.assertIn("explanationText", targets[0].allowed_fields)
        self.assertNotIn("questionBodyText", targets[0].allowed_fields)

    def test_parses_only_allowed_problem_fields(self):
        targets = candidate_targets("q1", "explanation", self.plan())
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "questionResults": [
                {
                    "questionId": "q1",
                    "status": "candidate",
                    "summary": "解説を整えた。",
                    "updates": [
                        {
                            "targetId": "q1:explanation",
                            "setFields": [
                                {
                                    "field": "explanationText",
                                    "valueJson": '["正しい。理由。"]',
                                }
                            ],
                            "unsetFields": [],
                        }
                    ],
                }
            ],
        }

        result = parse_candidates(json.dumps(payload), ["q1"], {"q1": targets})

        self.assertEqual(result[0].updates[0].set_fields["explanationText"][0], "正しい。理由。")
        self.assertEqual(output_schema(["q1"], {"q1": targets})["properties"]["schemaVersion"]["const"], SCHEMA_VERSION)

    def test_output_schema_uses_only_strict_objects(self):
        targets = candidate_targets("q1", "explanation", self.plan())
        schema = output_schema(["q1"], {"q1": targets})

        def assert_strict(value):
            if isinstance(value, dict):
                self.assertNotIn("uniqueItems", value)
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                    self.assertEqual(
                        set(value.get("required") or []),
                        set((value.get("properties") or {}).keys()),
                    )
                for child in value.values():
                    assert_strict(child)
            elif isinstance(value, list):
                for child in value:
                    assert_strict(child)

        assert_strict(schema)

    def test_isolates_disallowed_field_to_its_question(self):
        targets_q1 = candidate_targets("q1", "explanation", self.plan())
        targets_q2 = candidate_targets("q2", "explanation", self.plan())
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "questionResults": [
                {
                    "questionId": "q1",
                    "status": "candidate",
                    "summary": "不正な変更",
                    "updates": [
                        {
                            "targetId": "q1:explanation",
                            "setFields": [
                                {"field": "questionBodyText", "valueJson": '"変更"'}
                            ],
                            "unsetFields": [],
                        }
                    ],
                },
                {
                    "questionId": "q2",
                    "status": "candidate",
                    "summary": "正常な候補",
                    "updates": [],
                },
            ],
        }

        result = parse_candidates(
            payload,
            ["q1", "q2"],
            {"q1": targets_q1, "q2": targets_q2},
        )

        self.assertEqual(result[0].status, "blocked")
        self.assertIn("許可されていないfield", result[0].summary)
        self.assertEqual(result[1].status, "candidate")

    def test_content_validator_is_question_scoped(self):
        targets = candidate_targets("q1", "explanation", self.plan())
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "解説を整えた。",
                        "updates": [
                            {
                                "targetId": "q1:explanation",
                                "setFields": [
                                    {
                                        "field": "explanationText",
                                        "valueJson": '["正しい。理由。"]',
                                    }
                                ],
                                "unsetFields": [],
                            }
                        ],
                    }
                ],
            },
            ["q1"],
            {"q1": targets},
        )[0]

        errors = validate_candidate_content(
            candidate,
            targets,
            {"choiceTextList": ["選択肢"], "correctChoiceText": ["正しい"]},
        )

        self.assertEqual(errors, ())

    def test_law_audit_schema_version_is_not_exposed_to_model(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/18_law_context_prepared/patch.json",
                "output/sample/questions_json/2026/21_explanationText_added/patch.json",
                "output/sample/questions_json/2026/23_correctChoiceText_fixed/patch.json",
            ],
            "allowedWriteFiles": [
                "output/sample/questions_json/2026/law_revision_audit/patch.json"
            ],
        }
        targets = candidate_targets("q1", "law_audit", plan)
        audit_target = next(target for target in targets if target.role == "law_audit")
        self.assertNotIn("schemaVersion", audit_target.allowed_fields)
        self.assertEqual(
            audit_target.prompt_value()["fieldRules"]["auditStatus"][
                "allowedValues"
            ],
            [
                "same_as_current",
                "updated_to_current_law",
                "hold",
                "not_law_related",
            ],
        )
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "監査済み",
                        "updates": [
                            {
                                "targetId": audit_target.target_id,
                                "setFields": [
                                    {
                                        "field": "auditStatus",
                                        "valueJson": '"not_law_related"',
                                    }
                                ],
                                "unsetFields": [],
                            }
                        ],
                    }
                ],
            },
            ["q1"],
            {"q1": targets},
        )[0]

        self.assertEqual(
            validate_candidate_content(
                candidate,
                targets,
                {"choiceTextList": [], "correctChoiceText": []},
            ),
            (),
        )

    def test_law_audit_routes_misplaced_fields_to_server_owned_targets(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/18_law_context_prepared/patch.json",
                "output/sample/questions_json/2026/21_explanationText_added/patch.json",
                "output/sample/questions_json/2026/23_correctChoiceText_fixed/patch.json",
            ],
            "allowedWriteFiles": [
                "output/sample/review/law_revision_audit/2026.jsonl"
            ],
        }
        targets = candidate_targets("q1", "law_audit", plan)
        explanation = next(
            target for target in targets if target.role == "explanation"
        )
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "監査結果を確定した。",
                        "updates": [
                            {
                                "targetId": explanation.target_id,
                                "setFields": [
                                    {
                                        "field": "correctChoiceText",
                                        "valueJson": '["正しい"]',
                                    },
                                    {
                                        "field": "holdReason",
                                        "valueJson": '"根拠不足"',
                                    },
                                ],
                                "unsetFields": [],
                            }
                        ],
                    }
                ],
            },
            ["q1"],
            {"q1": targets},
        )[0]
        fields_by_role = {
            next(
                target.role
                for target in targets
                if target.target_id == update.target_id
            ): update.set_fields
            for update in candidate.updates
        }

        self.assertEqual(candidate.status, "candidate")
        self.assertNotIn("explanation", fields_by_role)
        self.assertEqual(
            fields_by_role["correct_choice"]["correctChoiceText"],
            ["正しい"],
        )
        self.assertEqual(
            fields_by_role["law_audit"],
            {
                "correctChoiceText": ["正しい"],
                "holdReason": "根拠不足",
            },
        )

    def test_law_audit_normalizes_empty_tertiary_run_id(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/21_explanationText_added/patch.json",
            ],
            "allowedWriteFiles": [
                "output/sample/review/law_revision_audit/2026.jsonl"
            ],
        }
        targets = candidate_targets("q1", "law_audit", plan)
        audit = next(target for target in targets if target.role == "law_audit")
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "二次監査で維持した。",
                        "updates": [
                            {
                                "targetId": audit.target_id,
                                "setFields": [
                                    {
                                        "field": "tertiaryAuditRunId",
                                        "valueJson": "[]",
                                    }
                                ],
                                "unsetFields": [],
                            }
                        ],
                    }
                ],
            },
            ["q1"],
            {"q1": targets},
        )[0]

        self.assertIsNone(candidate.updates[0].set_fields["tertiaryAuditRunId"])
        self.assertEqual(
            audit.prompt_value()["fieldRules"]["tertiaryAuditRunId"]["type"],
            ["string", "null"],
        )


if __name__ == "__main__":
    unittest.main()
