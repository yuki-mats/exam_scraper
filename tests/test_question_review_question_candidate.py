import json
import unittest

from tools.question_review_console.question_candidate import (
    SCHEMA_VERSION,
    QuestionCandidateError,
    candidate_targets,
    output_schema,
    parse_candidates,
    validate_candidate_content,
    aggregate_answer_review_schema,
    parse_aggregate_answer_reviews,
)


class QuestionCandidateTest(unittest.TestCase):
    def test_aggregate_review_contract_has_no_prose_fields(self):
        schema = aggregate_answer_review_schema(["q1"])
        item = schema["properties"]["questionReviews"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertNotIn("summary", item["properties"])
        payload = {
            "schemaVersion": "aggregate-answer-review-batch/v1",
            "questionReviews": [
                {
                    "questionId": "q1",
                    "schemaVersion": "aggregate-answer-review/v1",
                    "sourceHash": "sha256:" + "0" * 64,
                    "classification": "non_target",
                    "spans": [],
                    "decision": "approve",
                    "issueCodes": [],
                    "reason": "文章は禁止",
                }
            ],
        }
        with self.assertRaisesRegex(QuestionCandidateError, "文章"):
            parse_aggregate_answer_reviews(payload, ["q1"])

    def test_aggregate_review_parser_rejects_duplicate_issue_codes(self):
        payload = {
            "schemaVersion": "aggregate-answer-review-batch/v1",
            "questionReviews": [{
                "questionId": "q1",
                "schemaVersion": "aggregate-answer-review/v1",
                "sourceHash": "sha256:" + "0" * 64,
                "classification": "hold",
                "spans": [],
                "decision": "hold",
                "issueCodes": ["ambiguous_target", "ambiguous_target"],
            }],
        }
        with self.assertRaisesRegex(QuestionCandidateError, "重複"):
            parse_aggregate_answer_reviews(payload, ["q1"])

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
        self.assertIn("suggestedQuestionDetailsByChoice", targets[0].allowed_fields)
        self.assertNotIn("suggestedQuestions", targets[0].allowed_fields)
        self.assertNotIn("questionBodyText", targets[0].allowed_fields)

    def test_partial_target_allows_only_supplementary_questions(self):
        plan = {
            **self.plan(),
            "selectedFieldsByStage": {
                "explanation": ["suggestedQuestionDetailsByChoice"]
            },
        }
        target = candidate_targets("q1", "explanation", plan)[0]

        self.assertEqual(
            target.allowed_fields,
            ("suggestedQuestionDetailsByChoice",),
        )
        self.assertNotIn("explanationText", target.prompt_value()["fieldRules"])

        result = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "選択外fieldを含む候補",
                        "updates": [
                            {
                                "targetId": "q1:explanation",
                                "setFields": [
                                    {
                                        "field": "explanationText",
                                        "valueJson": '["変更してはならない"]',
                                    }
                                ],
                                "unsetFields": [],
                            }
                        ],
                    }
                ],
            },
            ["q1"],
            {"q1": (target,)},
        )

        self.assertEqual(result[0].status, "blocked")
        self.assertIn("許可されていないfield", result[0].summary)

    def test_explanation_target_exposes_nested_supplement_contract(self):
        target = candidate_targets("q1", "explanation", self.plan())[0]
        rules = target.prompt_value()["fieldRules"]

        self.assertIn("問題共通の1本", rules["explanationText"]["description"])
        supplement = rules["suggestedQuestionDetailsByChoice"]
        self.assertIn("基本解説に答えがある", supplement["description"])
        self.assertIn("追加情報がなければ必ず空配列", supplement["description"])
        self.assertIn("計算方法、式、代入、途中計算又は答え", supplement["description"])
        self.assertFalse(supplement["items"]["additionalProperties"])
        self.assertEqual(
            supplement["items"]["required"], ["choiceIndex", "items"]
        )
        item_rule = supplement["items"]["properties"]["items"]["items"]
        self.assertFalse(item_rule["additionalProperties"])
        self.assertEqual(item_rule["required"], ["question", "answer"])

    def test_correct_choice_target_requires_canonical_full_choice_markers(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/23_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        target = candidate_targets("q1", "correct_choice", plan)[0]
        rule = target.prompt_value()["fieldRules"]["correctChoiceText"]

        self.assertIn("choiceTextListと必ず同じ件数", rule["description"])
        self.assertIn("表記ゆれは使わない", rule["description"])
        self.assertEqual(rule["items"]["allowedValues"], ["正しい", "間違い"])

    def test_question_intent_target_cannot_update_correct_choice(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/15_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        target = candidate_targets("q1", "question_intent", plan)[0]

        self.assertEqual(target.allowed_fields, ("questionIntent",))
        self.assertNotIn("fieldRules", target.prompt_value())

    def test_question_type_target_rejects_single_choice(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/10_questionType_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "question_type", plan)
        rules = targets[0].prompt_value()["fieldRules"]
        self.assertEqual(
            rules["questionType"]["allowedValues"],
            [
                "true_false",
                "flash_card",
                "group_choice",
            ],
        )
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "単一選択と判定した。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"single_choice"',
                                    },
                                    {
                                        "field": "isCalculationQuestion",
                                        "valueJson": "true",
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

        errors = validate_candidate_content(
            candidate,
            targets,
            {
                "examYear": "2026",
                "choiceTextList": ["1", "2"],
                "correctChoiceText": ["正しい", "間違い"],
            },
        )

        self.assertIn("公式問題", errors[0])

    def test_question_type_target_rejects_single_choice_without_exam_year(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/custom/10_questionType_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "question_type", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "公式の独自問題を単一選択形式にする。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"single_choice"',
                                    },
                                    {
                                        "field": "isCalculationQuestion",
                                        "valueJson": "false",
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

        errors = validate_candidate_content(
            candidate,
            targets,
            {"choiceTextList": ["A", "B"], "correctChoiceText": ["正しい", "間違い"]},
        )

        self.assertIn("examYearの有無にかかわらず", errors[0])

    def test_question_type_target_rejects_fill_in_blank_without_exam_year(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/custom/10_questionType_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "question_type", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "公式の独自問題を穴埋め形式にする。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"fill_in_blank"',
                                    },
                                    {
                                        "field": "isCalculationQuestion",
                                        "valueJson": "false",
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

        errors = validate_candidate_content(
            candidate,
            targets,
            {
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
            },
        )

        self.assertIn("examYearの有無にかかわらず", errors[0])

    def test_question_type_target_allows_flash_card_without_exam_year(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/custom/10_questionType_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "question_type", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "公式の独自問題を想起型にする。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"flash_card"',
                                    },
                                    {
                                        "field": "isCalculationQuestion",
                                        "valueJson": "false",
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

        errors = validate_candidate_content(
            candidate,
            targets,
            {
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
            },
        )

        self.assertEqual(errors, ())

    def test_law_audit_target_exposes_required_choice_arrays(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/21_explanationText_added/patch.json"
            ],
            "allowedWriteFiles": [
                "output/sample/review/law_revision_audit/2026_law_revision_audit.jsonl"
            ],
        }
        targets = candidate_targets("q1", "law_audit", plan)
        audit = next(target for target in targets if target.role == "law_audit")
        rules = audit.prompt_value()["fieldRules"]

        self.assertIn("lawContextForExplanation", audit.allowed_fields)
        self.assertIn("answer_result_text", audit.allowed_fields)
        self.assertIn("examTimeDecision", audit.allowed_fields)
        self.assertIn("currentLawDecision", audit.allowed_fields)
        self.assertIn("choiceTextListと必ず同じ件数", rules["lawReferences"]["description"])
        self.assertIn("choiceTextListと必ず同じ件数", rules["examTimeDecision"]["description"])
        self.assertIn("choiceTextListと必ず同じ件数", rules["currentLawDecision"]["description"])

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

    def test_content_validator_rejects_choice_only_suggestions(self):
        targets = candidate_targets("q1", "explanation", self.plan())
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "補足を作成した。",
                        "updates": [
                            {
                                "targetId": "q1:explanation",
                                "setFields": [
                                    {
                                        "field": "suggestedQuestionDetailsByChoice",
                                        "valueJson": json.dumps(
                                            [
                                                {
                                                    "choiceIndex": 1,
                                                    "items": [
                                                        {
                                                            "question": "なぜ？",
                                                            "answer": "根拠。",
                                                        }
                                                    ],
                                                }
                                            ],
                                            ensure_ascii=False,
                                        ),
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
            {
                "questionType": "flash_card",
                "choiceTextList": ["正答", "誤答"],
                "correctChoiceText": ["正しい", "間違い"],
            },
        )

        self.assertIn("isChoiceOnly", errors[0])

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
        audit_rules = audit_target.prompt_value()["fieldRules"]
        self.assertIn(
            "件数を満たすために作らない",
            audit_rules["suggestedQuestionDetailsByChoice"]["description"],
        )
        self.assertIn(
            "具体的な法令名",
            audit_rules["explanationText"]["description"],
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

    def test_law_audit_normalizes_unset_tertiary_run_id_to_null(self):
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
                        "summary": "三次監査は不要と判断した。",
                        "updates": [
                            {
                                "targetId": audit.target_id,
                                "setFields": [],
                                "unsetFields": ["tertiaryAuditRunId"],
                            }
                        ],
                    }
                ],
            },
            ["q1"],
            {"q1": targets},
        )[0]

        self.assertIsNone(candidate.updates[0].set_fields["tertiaryAuditRunId"])
        self.assertNotIn(
            "tertiaryAuditRunId", candidate.updates[0].unset_fields
        )
        self.assertIn(
            "unsetFieldsへ入れず",
            audit.prompt_value()["fieldRules"]["tertiaryAuditRunId"][
                "description"
            ],
        )


if __name__ == "__main__":
    unittest.main()
