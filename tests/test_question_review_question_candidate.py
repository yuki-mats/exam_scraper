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
    def test_candidate_requires_every_selected_field(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/"
                "10_questionType_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "question_type": ["questionType", "isCalculationQuestion"]
            },
        }
        targets = candidate_targets("q1", "question_type", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "問題形式だけを返した。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"true_false"',
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
                "questionBodyText": "正しい記述はどれか。",
                "choiceTextList": ["記述A", "記述B"],
                "isCalculationQuestion": False,
            },
        )

        self.assertIn(
            "選択された更新fieldの候補がありません: isCalculationQuestion。"
            "各fieldを独立に確定できない場合は、この問題をblockedにしてください。",
            errors,
        )

    def test_candidate_cannot_complete_an_independent_field_by_unsetting_it(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/"
                "15_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "question_intent": ["questionIntent"]
            },
        }
        targets = candidate_targets("q1", "question_intent", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "設問意図を削除した。",
                        "updates": [
                            {
                                "targetId": "q1:question_intent",
                                "setFields": [],
                                "unsetFields": ["questionIntent"],
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
            {"questionIntent": "select_correct"},
        )

        self.assertIn(
            "選択された更新fieldの候補がありません: questionIntent。"
            "各fieldを独立に確定できない場合は、この問題をblockedにしてください。",
            errors,
        )

    def test_candidate_rejects_invalid_question_intent_value(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/"
                "15_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "question_intent", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "不正な値を返した。",
                        "updates": [
                            {
                                "targetId": "q1:question_intent",
                                "setFields": [
                                    {
                                        "field": "questionIntent",
                                        "valueJson": '"banana"',
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

        self.assertIn(
            "questionIntentがselect_correct又はselect_incorrectではありません。",
            validate_candidate_content(candidate, targets, {}),
        )

    def test_candidate_rejects_non_string_question_set_id(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/"
                "22_questionSetId_linked/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "question_set", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "不正な値を返した。",
                        "updates": [
                            {
                                "targetId": "q1:question_set",
                                "setFields": [
                                    {
                                        "field": "questionSetId",
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

        self.assertIn(
            "questionSetIdが非空stringではありません。",
            validate_candidate_content(candidate, targets, {}),
        )

    def test_correct_choice_candidate_requires_confirmed_question_intent(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/2026/"
                "23_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "correct_choice", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "選択肢を判定した。",
                        "updates": [
                            {
                                "targetId": "q1:correct_choice",
                                "setFields": [
                                    {
                                        "field": "correctChoiceText",
                                        "valueJson": '["正しい","間違い"]',
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

        self.assertIn(
            "correctChoiceTextの照合に必要なquestionIntentが"
            "select_correct又はselect_incorrectではありません。",
            validate_candidate_content(
                candidate,
                targets,
                {
                    "questionType": "true_false",
                    "choiceTextList": ["記述A", "記述B"],
                },
            ),
        )

    def test_aggregate_review_contract_has_no_prose_fields(self):
        schema = aggregate_answer_review_schema(["q1"])
        item = schema["properties"]["questionReviews"]["items"]
        self.assertFalse(item["additionalProperties"])
        for forbidden in (
            "summary",
            "questionBodyText",
            "spans",
            "start",
            "end",
            "truthAnswer",
        ):
            self.assertNotIn(forbidden, item["properties"])
        self.assertIn("candidateId", item["required"])
        payload = {
            "schemaVersion": "aggregate-answer-review-batch/v2",
            "questionReviews": [
                {
                    "questionId": "q1",
                    "schemaVersion": "aggregate-answer-review/v2",
                    "sourceHash": "sha256:" + "0" * 64,
                    "classification": "non_target",
                    "candidateId": None,
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
            "schemaVersion": "aggregate-answer-review-batch/v2",
            "questionReviews": [{
                "questionId": "q1",
                "schemaVersion": "aggregate-answer-review/v2",
                "sourceHash": "sha256:" + "0" * 64,
                "classification": "hold",
                "candidateId": None,
                "decision": "hold",
                "issueCodes": ["ambiguous_target", "ambiguous_target"],
            }],
        }
        with self.assertRaisesRegex(QuestionCandidateError, "重複"):
            parse_aggregate_answer_reviews(payload, ["q1"])

    def test_aggregate_review_parser_accepts_only_question_candidate_id(self):
        payload = {
            "schemaVersion": "aggregate-answer-review-batch/v2",
            "questionReviews": [{
                "questionId": "q1",
                "schemaVersion": "aggregate-answer-review/v2",
                "sourceHash": "sha256:" + "0" * 64,
                "classification": "target",
                "candidateId": "candidate:allowed",
                "decision": "approve",
                "issueCodes": [],
            }],
        }
        parsed = parse_aggregate_answer_reviews(
            payload,
            ["q1"],
            {"q1": ["candidate:allowed"]},
        )
        self.assertEqual(parsed["q1"]["candidateId"], "candidate:allowed")

        payload["questionReviews"][0]["candidateId"] = "candidate:other"
        with self.assertRaisesRegex(QuestionCandidateError, "対象外"):
            parse_aggregate_answer_reviews(
                payload,
                ["q1"],
                {"q1": ["candidate:allowed"]},
            )

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
        self.assertIn("explanationReferences", targets[0].allowed_fields)
        self.assertIn("suggestedQuestionDetailsByChoice", targets[0].allowed_fields)
        self.assertNotIn("suggestedQuestions", targets[0].allowed_fields)
        self.assertNotIn("questionBodyText", targets[0].allowed_fields)

    def test_originalize_candidate_requires_non_empty_public_content(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/independent/"
                "05_originalized/q1.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "originalize": ["questionBodyText", "choiceTextList"]
            },
        }
        targets = candidate_targets("q1", "originalize", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "空の公開本文と選択肢を返した。",
                        "updates": [
                            {
                                "targetId": "q1:originalized",
                                "setFields": [
                                    {
                                        "field": "questionBodyText",
                                        "valueJson": '""',
                                    },
                                    {
                                        "field": "choiceTextList",
                                        "valueJson": '["A", ""]',
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

        errors = validate_candidate_content(candidate, targets, {})

        self.assertIn("questionBodyTextが非空stringではありません。", errors)
        self.assertIn("choiceTextListが非空stringの配列ではありません。", errors)

    def test_originalize_candidate_rejects_source_identical_body_and_choices(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/independent/"
                "05_originalized/q1.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "originalize": ["answer_result_text"]
            },
        }
        targets = candidate_targets("q1", "originalize", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "正解表示だけを変更した。",
                        "updates": [
                            {
                                "targetId": "q1:originalized",
                                "setFields": [
                                    {
                                        "field": "answer_result_text",
                                        "valueJson": '"正解は「B」です。"',
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
        source = {
            "questionBodyText": "元の問題文",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["間違い", "正しい"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は2です。",
        }

        errors = validate_candidate_content(candidate, targets, source)

        self.assertIn(
            "05_originalizedの問題文と選択肢が00_sourceと完全一致しています。",
            errors,
        )

    def test_originalize_candidate_allows_source_identical_body_when_choice_changes(
        self,
    ):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/independent/"
                "05_originalized/q1.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "originalize": ["choiceTextList"]
            },
        }
        targets = candidate_targets("q1", "originalize", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "設問を維持し、選択肢を一つだけ自然に整えた。",
                        "updates": [
                            {
                                "targetId": "q1:originalized",
                                "setFields": [
                                    {
                                        "field": "choiceTextList",
                                        "valueJson": '["Aを構成する。", "B"]',
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
        source = {
            "questionBodyText": "元の問題文",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["間違い", "正しい"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は2です。",
        }

        errors = validate_candidate_content(candidate, targets, source)

        self.assertEqual(errors, ())

    def test_originalize_candidate_allows_choice_reordering_with_source_body(
        self,
    ):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/independent/"
                "05_originalized/q1.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "originalize": [
                    "choiceTextList",
                    "correctChoiceText",
                    "answer_result_text",
                ]
            },
        }
        targets = candidate_targets("q1", "originalize", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "設問を維持し、選択肢の順番を入れ替えた。",
                        "updates": [
                            {
                                "targetId": "q1:originalized",
                                "setFields": [
                                    {
                                        "field": "choiceTextList",
                                        "valueJson": '["B", "A"]',
                                    },
                                    {
                                        "field": "correctChoiceText",
                                        "valueJson": '["正しい", "間違い"]',
                                    },
                                    {
                                        "field": "answer_result_text",
                                        "valueJson": '"正解は1です。"',
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
        source = {
            "questionBodyText": "元の問題文",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["間違い", "正しい"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は2です。",
        }

        errors = validate_candidate_content(candidate, targets, source)

        self.assertEqual(errors, ())

    def test_originalize_candidate_allows_source_identical_choices_when_body_changes(
        self,
    ):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/independent/"
                "05_originalized/q1.json"
            ],
            "allowedWriteFiles": [],
            "selectedFieldsByStage": {
                "originalize": ["questionBodyText"]
            },
        }
        targets = candidate_targets("q1", "originalize", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "場面と条件を独自問題として組み直した。",
                        "updates": [
                            {
                                "targetId": "q1:originalized",
                                "setFields": [
                                    {
                                        "field": "questionBodyText",
                                        "valueJson": '"独自の場面と条件に組み直した問題文"',
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
        source = {
            "questionBodyText": "元の問題文",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["間違い", "正しい"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は2です。",
        }

        errors = validate_candidate_content(candidate, targets, source)

        self.assertEqual(errors, ())

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
        self.assertIn(
            "「正しい。」又は「間違い。」で始める",
            rules["explanationText"]["description"],
        )
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

    def test_all_law_field_roles_expose_shared_choice_aligned_contract(self):
        law_context = candidate_targets(
            "q1",
            "law_context",
            {
                "allowedPatchFiles": [
                    "output/sample/questions_json/2026/18_law_context_prepared/patch.json"
                ],
                "allowedWriteFiles": [],
            },
        )[0]
        explanation = candidate_targets("q1", "explanation", self.plan())[0]
        audit_targets = candidate_targets(
            "q1",
            "law_audit",
            {
                "allowedPatchFiles": [
                    "output/sample/questions_json/2026/21_explanationText_added/patch.json"
                ],
                "allowedWriteFiles": [
                    "output/sample/review/law_revision_audit/2026_law_revision_audit.jsonl"
                ],
            },
        )
        audit = next(target for target in audit_targets if target.role == "law_audit")

        role_rules = [
            target.prompt_value()["fieldRules"]
            for target in (law_context, explanation, audit)
        ]
        for rules in role_rules:
            self.assertEqual(rules["isLawRelated"]["type"], "boolean")
            self.assertEqual(
                rules["lawGroundedExplanationNotNeeded"]["type"],
                "boolean",
            )
            self.assertIn(
                "choiceTextListと必ず同じ件数",
                rules["lawReferences"]["description"],
            )
            self.assertEqual(rules["lawReferences"]["items"]["type"], "array")
            self.assertEqual(rules["lawContextForExplanation"]["type"], "string")
        self.assertEqual(
            role_rules[0]["lawReferences"],
            role_rules[1]["lawReferences"],
        )
        self.assertEqual(
            role_rules[1]["lawReferences"],
            role_rules[2]["lawReferences"],
        )

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
        self.assertEqual(
            target.prompt_value()["fieldRules"]["questionIntent"]["allowedValues"],
            ["select_correct", "select_incorrect"],
        )

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
        self.assertIn(
            "group_choiceは選択肢群から正答を1つだけ選ぶ",
            rules["questionType"]["description"],
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

    def test_question_type_candidate_detects_ambiguous_group_choice_answer(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/custom/"
                "10_questionType_fixed/patch.json"
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
                        "summary": "候補比較型と判定した。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"group_choice"',
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
                "questionIntent": "select_correct",
                "choiceTextList": ["候補A", "候補B", "候補C"],
                "correctChoiceText": ["正しい", "正しい", "間違い"],
            },
        )

        self.assertTrue(
            any(
                "group_choiceは公開時に正答を1件だけ必要" in error
                for error in errors
            )
        )

    def test_question_type_candidate_does_not_trust_unreviewed_answer_count(self):
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
                        "summary": "候補群から1つを選ぶ形式と判定した。",
                        "updates": [
                            {
                                "targetId": "q1:question_type",
                                "setFields": [
                                    {
                                        "field": "questionType",
                                        "valueJson": '"group_choice"',
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
                "choiceTextList": ["候補A", "候補B", "候補C"],
                "correctChoiceText": ["正しい", "正しい", "間違い"],
            },
        )

        self.assertEqual(errors, ())

    def test_correct_choice_candidate_reports_cross_field_mismatch(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/custom/23_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "correct_choice", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "各選択肢を根拠から精査した。",
                        "updates": [
                            {
                                "targetId": "q1:correct_choice",
                                "setFields": [
                                    {
                                        "field": "correctChoiceText",
                                        "valueJson": '["正しい","正しい","間違い"]',
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
                "questionType": "group_choice",
                "questionIntent": "select_correct",
                "choiceTextList": ["候補A", "候補B", "候補C"],
                "correctChoiceText": ["間違い", "正しい", "間違い"],
            },
        )

        self.assertTrue(
            any(
                "questionType、questionIntent、correctChoiceText" in error
                for error in errors
            )
        )

    def test_true_false_correct_choice_allows_multiple_true_statements(self):
        plan = {
            "allowedPatchFiles": [
                "output/sample/questions_json/custom/23_correctChoiceText_fixed/patch.json"
            ],
            "allowedWriteFiles": [],
        }
        targets = candidate_targets("q1", "correct_choice", plan)
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "各記述を独立して判定した。",
                        "updates": [
                            {
                                "targetId": "q1:correct_choice",
                                "setFields": [
                                    {
                                        "field": "correctChoiceText",
                                        "valueJson": '["正しい","正しい","間違い"]',
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
                "questionType": "true_false",
                "questionIntent": "select_correct",
                "choiceTextList": ["記述A", "記述B", "記述C"],
                "correctChoiceText": ["間違い", "間違い", "間違い"],
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

    def test_content_validator_rejects_bad_explanation_style_before_write(self):
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
                                        "valueJson": '["理由から正しい。"]',
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
                "questionType": "true_false",
                "choiceTextList": ["選択肢"],
                "correctChoiceText": ["正しい"],
            },
        )

        self.assertEqual(
            errors,
            ("選択肢1: 解説は「正しい。」又は「間違い。」で始めてください。",),
        )

    def test_content_validator_rejects_invalid_explanation_reference(self):
        targets = candidate_targets("q1", "explanation", self.plan())
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "参照先を追加した。",
                        "updates": [
                            {
                                "targetId": "q1:explanation",
                                "setFields": [
                                    {
                                        "field": "explanationReferences",
                                        "valueJson": json.dumps(
                                            [
                                                {
                                                    "title": "非HTTPS資料",
                                                    "sourceUrl": "http://example.com",
                                                    "referenceDate": "2026-07-23",
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
                "questionType": "true_false",
                "choiceTextList": ["選択肢"],
                "correctChoiceText": ["正しい"],
            },
        )

        self.assertTrue(any("HTTPS URL" in error for error in errors))

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
        self.assertEqual(
            audit_rules["lawRevisionFacts"]["type"],
            ["object", "array"],
        )
        law_reference_item = audit_rules["lawReferences"]["items"]["items"]
        self.assertIn("lawId", law_reference_item["required"])
        self.assertIn("verificationStatus", law_reference_item["required"])
        law_references = [
            [
                {
                    "role": "current_basis",
                    "scope": "choice",
                    "choiceIndex": 0,
                    "lawId": "123AC0000000001",
                    "lawTitle": "試験法",
                    "referenceDate": "2026-07-22",
                    "article": "1",
                    "verificationStatus": "verified",
                    "source": "egov_xml",
                }
            ]
        ]
        law_revision_facts = [
            {
                "auditStatus": "same_as_current",
                "reviewState": "secondary_verified",
                "examTime": {"correctChoiceText": "正しい"},
                "current": {"correctChoiceText": "正しい"},
                "evidenceSummary": {
                    "verdict": "same_as_current",
                    "explanationText": "試験法第1条を確認した。",
                },
            }
        ]
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
                                        "valueJson": '"same_as_current"',
                                    },
                                    {
                                        "field": "reviewState",
                                        "valueJson": '"secondary_verified"',
                                    },
                                    {
                                        "field": "sourceSummary",
                                        "valueJson": '"e-Govの試験法第1条を確認した。"',
                                    },
                                    {
                                        "field": "verificationSummary",
                                        "valueJson": '"条文本文と正誤が一致した。"',
                                    },
                                    {
                                        "field": "reconciliationStatus",
                                        "valueJson": '"matched"',
                                    },
                                    {
                                        "field": "examTimeDecision",
                                        "valueJson": '["正しい"]',
                                    },
                                    {
                                        "field": "currentLawDecision",
                                        "valueJson": '["正しい"]',
                                    },
                                    {
                                        "field": "isLawRelated",
                                        "valueJson": "true",
                                    },
                                    {
                                        "field": "lawGroundedExplanationNotNeeded",
                                        "valueJson": "false",
                                    },
                                    {
                                        "field": "lawReferences",
                                        "valueJson": json.dumps(law_references, ensure_ascii=False),
                                    },
                                    {
                                        "field": "lawRevisionFacts",
                                        "valueJson": json.dumps(law_revision_facts, ensure_ascii=False),
                                    },
                                    {
                                        "field": "correctChoiceText",
                                        "valueJson": '["正しい"]',
                                    },
                                    {
                                        "field": "explanationText",
                                        "valueJson": '["正しい。試験法第1条に定められている。"]',
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

        self.assertEqual(
            validate_candidate_content(
                candidate,
                targets,
                {
                    "questionType": "true_false",
                    "questionIntent": "select_correct",
                    "choiceTextList": ["条文上の記述"],
                    "correctChoiceText": ["正しい"],
                },
            ),
            (),
        )

    def test_law_audit_target_fans_out_shared_fields_to_canonical_patches(self):
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
        audit = next(target for target in targets if target.role == "law_audit")
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "監査結果を各正本へ反映する。",
                        "updates": [
                            {
                                "targetId": audit.target_id,
                                "setFields": [
                                    {"field": "isLawRelated", "valueJson": "true"},
                                    {"field": "lawReferences", "valueJson": "[[{\"lawId\":\"x\"}]]"},
                                    {"field": "lawRevisionFacts", "valueJson": "[{\"auditStatus\":\"same_as_current\"}]"},
                                    {"field": "correctChoiceText", "valueJson": '["正しい"]'},
                                    {"field": "explanationText", "valueJson": '["正しい。根拠。"]'},
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
        self.assertEqual(
            set(fields_by_role),
            {"law_context", "explanation", "correct_choice", "law_audit"},
        )
        self.assertIn("lawReferences", fields_by_role["law_context"])
        self.assertIn("lawReferences", fields_by_role["explanation"])
        self.assertIn("lawRevisionFacts", fields_by_role["explanation"])
        self.assertEqual(
            fields_by_role["correct_choice"]["correctChoiceText"],
            ["正しい"],
        )
        self.assertIn("explanationText", fields_by_role["law_audit"])

    def test_law_audit_rejects_legacy_string_references_and_weak_facts(self):
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
        audit = next(target for target in targets if target.role == "law_audit")
        values = {
            "auditStatus": "same_as_current",
            "reviewState": "secondary_verified",
            "sourceSummary": "法令を確認した。",
            "verificationSummary": "正誤を照合した。",
            "reconciliationStatus": "matched",
            "examTimeDecision": ["正しい"],
            "currentLawDecision": ["正しい"],
            "isLawRelated": True,
            "lawReferences": [["試験法第1条"]],
            "lawRevisionFacts": {
                "auditStatus": "same_as_current",
                "reviewState": "secondary_verified",
                "current": {"correctChoiceText": ["正しい"]},
                "evidenceSummary": "文字列の要約",
            },
        }
        candidate = parse_candidates(
            {
                "schemaVersion": SCHEMA_VERSION,
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "candidate",
                        "summary": "旧形式の候補",
                        "updates": [
                            {
                                "targetId": audit.target_id,
                                "setFields": [
                                    {
                                        "field": field,
                                        "valueJson": json.dumps(
                                            value, ensure_ascii=False
                                        ),
                                    }
                                    for field, value in values.items()
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
                "questionType": "true_false",
                "choiceTextList": ["条文上の記述"],
                "correctChoiceText": ["正しい"],
            },
        )

        self.assertTrue(
            any("evidenceSummaryが非空object" in error for error in errors)
        )
        self.assertTrue(
            any("lawReferences[0][0]がobject" in error for error in errors)
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
