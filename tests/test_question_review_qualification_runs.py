from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import threading
import time
from pathlib import Path

from tests.qualification_run_test_support import *  # noqa: F403

from tools.question_review_console.codex_app_server import (
    CodexAppServerError,
    CodexControlRequestTimeoutError,
    CodexTerminalTurnFailedError,
    CodexTurnTimeoutError,
    SubscriptionGateError,
)
from tools.question_review_console.question_work_queue import (
    input_fingerprint,
    specialize_question_plan,
)
from tools.question_review_console.qualification_runs import (
    AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
    MANIFEST_CACHE_LIMIT,
    QuestionItemError,
    QuestionQueuePaused,
    QualificationRunCoordinator,
    _PipelineRuntimeTelemetry,
    _aggregate_answer_review_prompt,
    _aggregate_calculation_flag,
    _aggregate_downstream_source_evidence,
    _aggregate_review_source_records,
    _candidate_unset_fields,
    _canonical_document_guidance,
    _external_provider_failure,
    evaluation_rework_stage_codes,
    _isolated_turn_failure,
    _isolated_turn_timeout,
    _law_reference_discovery_plan,
    _prepared_candidate_envelope,
    _question_work_preview_group_summary,
    _question_work_target_identity,
    _question_plan_list_group_id,
    _restore_resume_target_aliases,
    _resume_orchestration_selections_match,
    _source_binding_accepts_identity,
    _server_law_audit_fields,
    _structured_candidate_stage_context,
    _structured_candidate_prompt,
    _trusted_source_answer_evidence,
    _validated_prepared_candidate,
    _validated_question_work_queue,
    _validated_projected_input_path,
    prepare_question_items_concurrently,
)
from tools.question_review_console.question_patch_proposal import (
    TargetResolutionCache,
    assert_target_resolvable,
)
import tools.question_review_console.question_patch_proposal as question_patch_proposal
from tools.question_review_console.question_candidate import (
    CandidateTarget,
    _semantic_field_rules,
)
from tools.question_review_console.model_backend import (
    MaintenanceAttemptRoute,
    ModelBackendError,
    ProfileModelRouter,
    parse_model_backend_config,
)
from scripts.common.question_identity import SourceIdentityBinding
from scripts.common.aggregate_answer_decomposition import (
    candidate_set_hash,
    generate_statement_candidates,
    source_text_hash,
)


_BaseFlowAppServer = FlowAppServer
_BasePerQuestionQueueAppServer = PerQuestionQueueAppServer


class QuestionWorkPreviewGroupSummaryTests(unittest.TestCase):
    @staticmethod
    def target(question_id, group_id):
        return {
            "id": question_id,
            "questionKey": question_id,
            "sourceQuestionKey": f"source:{question_id}",
            "reviewQuestionId": f"review:{question_id}",
            "sourceRecordRef": f"{group_id}.json#{question_id}",
            "listGroupId": group_id,
        }

    def plan(self):
        targets = [
            self.target("keep-1", "keep"),
            self.target("keep-2", "keep"),
            self.target("ping-1", "ping"),
        ]
        return {
            "kind": "orchestration",
            "scopeListGroupIds": ["keep", "ping"],
            "targetGroupIds": ["keep", "ping"],
            "targetCount": 3,
            "workItemCount": 6,
            "stageCount": 2,
            "stageIds": ["question_intent", "explanation"],
            "progressTargets": targets,
            "stagePlans": [
                {"stageId": "question_intent", "progressTargets": targets},
                {"stageId": "explanation", "progressTargets": targets},
            ],
        }

    def test_summary_is_derived_from_existing_question_work_plan(self):
        self.assertEqual(
            _question_work_preview_group_summary(self.plan()),
            [
                {"listGroupId": "keep", "questionCount": 2, "workItemCount": 4},
                {"listGroupId": "ping", "questionCount": 1, "workItemCount": 2},
            ],
        )

    def test_summary_skips_qualification_scope_without_question_targets(self):
        plan = {
            "kind": "human",
            "scopeListGroupIds": [],
            "targetGroupIds": ["keep", "ping"],
            "targetCount": 322,
            "workItemCount": 1,
            "stageIds": ["category_setup"],
            "progressTargets": [],
        }

        self.assertEqual(_question_work_preview_group_summary(plan), [])

    def test_target_identity_is_derived_from_actual_queue(self):
        identity = _question_work_target_identity(self.plan())

        self.assertEqual(identity["questionIds"], ["keep-1", "keep-2", "ping-1"])
        self.assertEqual(len(identity["workItemKeys"]), 6)
        self.assertEqual(identity["workItemCount"], 6)
        self.assertEqual(identity["stageCount"], 2)
        self.assertEqual(
            identity["questionIdsHash"],
            hashlib.sha256(b"keep-1\nkeep-2\nping-1\n").hexdigest(),
        )
        self.assertEqual(
            identity["stageSummary"],
            [
                {"stageId": "question_intent", "workItemCount": 3},
                {"stageId": "explanation", "workItemCount": 3},
            ],
        )

    def test_start_queue_skips_qualification_scope_without_question_work(self):
        executions, summary = _validated_question_work_queue(
            {
                "stageIds": ["category_setup"],
                "targetIdentity": None,
                "targetCount": 1,
                "workItemCount": 1,
                "progressTargets": [],
            }
        )

        self.assertEqual(executions, [])
        self.assertEqual(summary, {"questionCount": 0, "workItemCount": 0})

    def test_start_queue_requires_preview_identity_for_question_work(self):
        plan = self.plan()
        with self.assertRaisesRegex(QualificationRunError, "identity"):
            _validated_question_work_queue(plan)

        plan["targetIdentity"] = _question_work_target_identity(plan)
        executions, summary = _validated_question_work_queue(plan)

        self.assertEqual(len(executions), 3)
        self.assertEqual(summary["workItemCount"], 6)

    def test_target_identity_fails_closed_on_declared_total_mismatch(self):
        plan = self.plan()
        plan["targetCount"] = 4
        with self.assertRaises(QualificationRunError):
            _question_work_target_identity(plan)

    def test_target_identity_fails_closed_on_work_item_count_mismatch(self):
        plan = self.plan()
        plan["workItemCount"] = 5
        with self.assertRaises(QualificationRunError):
            _question_work_target_identity(plan)

    def test_target_identity_fails_closed_on_stage_count_mismatch(self):
        plan = self.plan()
        plan["stageCount"] = 3
        with self.assertRaises(QualificationRunError):
            _question_work_target_identity(plan)

    def test_target_identity_keeps_selection_and_actual_queue_counts_separate(self):
        plan = self.plan()
        plan["workItemCount"] = 3
        plan["stagePlans"][1]["targetCount"] = 0
        plan["stagePlans"][1]["progressTargets"] = []

        identity = _question_work_target_identity(plan)

        self.assertEqual(plan["workItemCount"], 3)
        self.assertEqual(identity["workItemCount"], 6)
        self.assertEqual(identity["stageCount"], 2)


    def test_evaluation_rework_keeps_mixed_selection_and_queue_counts_separate(self):
        plan = self.plan()
        for target in plan["progressTargets"]:
            target["stateHash"] = f"state-{target['id']}"
        snapshots = {
            "keep-1": {
                "status": "needs_rework",
                "stateHash": "state-keep-1",
                "reworkItems": [
                    {"stage": "02", "message": "設問意図を直す", "choiceIndexes": []}
                ],
            },
            "keep-2": {
                "status": "needs_rework",
                "stateHash": "state-keep-2",
                "reworkItems": [
                    {"stage": "03", "message": "解説を直す", "choiceIndexes": []}
                ],
            },
            "ping-1": {
                "status": "needs_rework",
                "stateHash": "state-ping-1",
                "reworkItems": [
                    {"stage": "02", "message": "設問意図を直す", "choiceIndexes": []},
                    {"stage": "03", "message": "解説を直す", "choiceIndexes": []},
                ],
            },
        }
        coordinator = object.__new__(QualificationRunCoordinator)

        coordinator._apply_evaluation_rework_plan(plan, snapshots)
        identity = _question_work_target_identity(plan)

        self.assertEqual(plan["selectionWorkItemCount"], 6)
        self.assertEqual(plan["workItemCount"], 4)
        self.assertEqual(identity["workItemCount"], 4)

    def test_target_identity_validates_explicit_resume_selection_and_queue(self):
        plan = self.plan()
        plan["selectionWorkItemCount"] = 3
        plan["stagePlans"][1]["targetCount"] = 0
        plan["stagePlans"][1]["progressTargets"] = []

        identity = _question_work_target_identity(plan)

        self.assertEqual(plan["selectionWorkItemCount"], 3)
        self.assertEqual(plan["workItemCount"], 6)
        self.assertEqual(identity["workItemCount"], 6)

        plan["workItemCount"] = 5
        with self.assertRaisesRegex(QualificationRunError, "queue workItemCount"):
            _question_work_target_identity(plan)

    def test_target_identity_fails_closed_on_invalid_queue_identity(self):
        base = [
            {
                "questionId": "q1",
                "stages": [{"workItemKey": "w1", "stageId": "question_intent"}],
            },
            {
                "questionId": "q2",
                "stages": [{"workItemKey": "w2", "stageId": "explanation"}],
            },
        ]
        cases = {
            "empty question id": (0, "questionId", ""),
            "empty work item key": (0, "workItemKey", ""),
            "empty stage id": (0, "stageId", ""),
            "duplicate question id": (1, "questionId", "q1"),
            "duplicate work item key": (1, "workItemKey", "w1"),
            "unknown stage": (0, "stageId", "unknown_stage"),
        }
        plan = self.plan()
        plan.update(
            targetCount=2,
            workItemCount=2,
            stagePlans=[
                {"stageId": "question_intent", "targetCount": 1},
                {"stageId": "explanation", "targetCount": 1},
            ],
        )
        for label, (index, field, value) in cases.items():
            executions = copy.deepcopy(base)
            if field == "questionId":
                executions[index][field] = value
            else:
                executions[index]["stages"][0][field] = value
            with self.subTest(label=label), patch(
                "tools.question_review_console.qualification_runs.build_question_executions",
                return_value=executions,
            ), self.assertRaises(QualificationRunError):
                _question_work_target_identity(plan)

    def test_summary_rejects_duplicate_unknown_and_mismatched_totals(self):
        malformed = self.plan()
        malformed["progressTargets"] = {}
        with self.assertRaises(QualificationRunError):
            _question_work_preview_group_summary(malformed)

        duplicate = self.plan()
        duplicate["progressTargets"].append(self.target("keep-1", "keep"))
        with self.assertRaises(QualificationRunError):
            _question_work_preview_group_summary(duplicate)

        unknown = self.plan()
        unknown["progressTargets"][0]["listGroupId"] = "unknown"
        with self.assertRaises(QualificationRunError):
            _question_work_preview_group_summary(unknown)

        mismatch = self.plan()
        mismatch["targetCount"] = 4
        with self.assertRaises(QualificationRunError):
            _question_work_preview_group_summary(mismatch)

    def test_summary_uses_real_queue_when_questions_start_at_different_stages(self):
        keep_targets = [
            self.target("keep-1", "keep"),
            self.target("keep-2", "keep"),
        ]
        ping_targets = [
            self.target("ping-1", "ping"),
            self.target("ping-2", "ping"),
        ]
        plan = {
            "kind": "orchestration",
            "scopeListGroupIds": ["keep", "ping"],
            "targetGroupIds": ["keep", "ping"],
            "targetCount": 4,
            "workItemCount": 4,
            "progressTargets": [*keep_targets, *ping_targets],
            "stageCount": 2,
            "stageIds": ["question_intent", "explanation"],
            "stagePlans": [
                {"stageId": "question_intent", "progressTargets": keep_targets},
                {
                    "stageId": "explanation",
                    "progressTargets": [*keep_targets, *ping_targets],
                },
            ],
        }

        self.assertEqual(
            _question_work_preview_group_summary(plan),
            [
                {"listGroupId": "keep", "questionCount": 2, "workItemCount": 4},
                {"listGroupId": "ping", "questionCount": 2, "workItemCount": 2},
            ],
        )


class LegacyRunModelProfileResumeTests(unittest.TestCase):
    @staticmethod
    def app_server():
        return SimpleNamespace(
            configured=True,
            snapshot_for=lambda name: {
                "name": name,
                "fingerprint": f"fingerprint:{name}",
                "limits": {
                    "questionParallelism": 1,
                    "llmCallConcurrency": 1,
                    "auditBatchQuestions": 5,
                    "auditBatchInputBytes": 120000,
                },
                "roles": {},
            },
        )

    def test_legacy_run_rejects_resume_with_non_codex_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory), FakeWorkflow(), FakeSynchronizer(),
                JobManager(), "secret", app_server=self.app_server(),
            )
            plan = FakeWorkflow().plan("sample", "explanation")
            previous = coordinator.store.create(plan, status="interrupted")
            with self.assertRaisesRegex(
                QualificationRunError, "旧runはcodex_onlyでのみ",
            ):
                coordinator._preview_uncached(
                    "sample", "explanation", "refresh",
                    resumed_from=previous["runId"],
                    model_profile="local_generate_codex_audit",
                    _prepared_plan=plan,
                )
            self.assertIsNone(
                coordinator.store.get("sample", previous["runId"])["llmProfile"]
            )

    def test_legacy_run_allows_codex_only_snapshot_without_rewriting_old_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory), FakeWorkflow(), FakeSynchronizer(),
                JobManager(), "secret", app_server=self.app_server(),
            )
            plan = FakeWorkflow().plan("sample", "explanation")
            previous = coordinator.store.create(plan, status="interrupted")
            preview = coordinator._preview_uncached(
                "sample", "explanation", "refresh",
                resumed_from=previous["runId"], model_profile="codex_only",
                _prepared_plan=plan,
            )
            self.assertEqual(preview["llmProfile"]["name"], "codex_only")
            self.assertIsNone(
                coordinator.store.get("sample", previous["runId"])["llmProfile"]
            )


class EvaluationReworkStageTests(unittest.TestCase):
    def test_content_rework_runs_originalize_before_answer_and_explanation(self):
        stages = evaluation_rework_stage_codes(
            {
                "answerMappingMatched": False,
                "reworkItems": [
                    {
                        "stage": "05",
                        "message": "問題文の条件を直す",
                        "choiceIndexes": [0],
                    }
                ],
            }
        )

        self.assertEqual(stages, ["05", "02a", "03"])

    def test_correct_answer_rework_also_rebuilds_explanation(self):
        stages = evaluation_rework_stage_codes(
            {
                "reworkItems": [
                    {
                        "stage": "02a",
                        "message": "正答を再確認する",
                        "choiceIndexes": [1],
                    }
                ]
            }
        )

        self.assertEqual(stages, ["02a", "03"])

    def test_law_audit_rework_includes_law_context_prerequisite(self):
        stages = evaluation_rework_stage_codes(
            {
                "reworkItems": [
                    {
                        "stage": "03b",
                        "message": "法令根拠を再確認する",
                        "choiceIndexes": [0],
                    }
                ]
            }
        )

        self.assertEqual(stages, ["02b", "03b"])

    def test_question_type_field_rule_matches_single_result_calculation_policy(
        self,
    ):
        rules = _semantic_field_rules(
            "question-1",
            (
                CandidateTarget(
                    target_id="question-1:question_type",
                    role="question_type",
                    path="output/sample/10_questionType_fixed/question.json",
                    allowed_fields=("questionType", "isCalculationQuestion"),
                ),
            ),
        )

        description = rules["questionType"]["description"]
        self.assertIn(
            "単一の計算結果に最も近い数値候補を選ぶ問題もflash_card",
            description,
        )
        self.assertIn(
            "肢ごとに独立して正誤を判定する問題はtrue_false",
            description,
        )

        prompt = (
            Path(__file__).resolve().parents[1]
            / "prompt"
            / "01_prompt_fix_questionType.md"
        ).read_text(encoding="utf-8")
        self.assertIn("問題文の共通述語を各候補に補って判定", prompt)
        self.assertIn("他の候補の判定と切り離して", prompt)
        self.assertIn("選択肢を見る前に導き", prompt)


class PipelineTelemetryContractTests(unittest.TestCase):
    def test_question_window_segment_boundary_discards_stale_release(self):
        telemetry = _PipelineRuntimeTelemetry(
            model_capacity=10,
            patch_tool_capacity=10,
        )
        telemetry.question_window_released(
            "previous-segment-question",
            observed_monotonic=1.0,
        )

        telemetry.question_window_segment_started()
        latency = telemetry.question_window_admitted(
            "new-segment-question",
            source="waiting",
            observed_monotonic=2.0,
        )

        self.assertIsNone(latency)
        self.assertEqual(
            telemetry.question_window_snapshot()["refillLatencySeconds"][
                "count"
            ],
            0,
        )

    def test_patch_tool_reports_tool_and_actual_path_lock_concurrency(self):
        telemetry = _PipelineRuntimeTelemetry(
            model_capacity=1,
            patch_tool_capacity=3,
        )
        path_pairs = {
            "child-1": (
                "output/sample/questions_json/2025/21_explanationText_added/"
                "patch.json",
            ),
            "child-2": (
                "output/sample/questions_json/2026/21_explanationText_added/"
                "patch.json",
            ),
        }
        for child_id, paths in path_pairs.items():
            telemetry.patch_tool_started(
                child_id,
                queue_wait_seconds=0.0,
            )
            telemetry.patch_lock_acquired(
                child_id,
                paths,
                0.01,
            )

        snapshot = telemetry.patch_tool_snapshot()
        self.assertEqual(snapshot["peakInFlight"], 2)
        self.assertEqual(snapshot["lockHeldPeakInFlight"], 2)
        self.assertEqual(
            snapshot["lockHeldPeakInFlightByPath"],
            {
                path_pairs["child-1"][0]: 1,
                path_pairs["child-2"][0]: 1,
            },
        )
        for child_id in path_pairs:
            telemetry.patch_lock_released(child_id)
            telemetry.patch_tool_finished(child_id)
        completed = telemetry.patch_tool_snapshot()
        self.assertEqual(completed["inFlight"], 0)
        self.assertEqual(completed["lockHeldInFlight"], 0)
        self.assertEqual(completed["finishedCount"], 2)


def _question_attempts(store, qualification, run):
    attempts = []
    for execution in run.get("questionExecutions") or []:
        question_id = str(execution.get("questionId") or "")
        detail = store.question_detail(
            qualification,
            str(run["runId"]),
            question_id,
        )
        attempts.extend(
            store.get(qualification, str(value["attemptId"]))
            for value in (
                detail.get("attemptArtifacts") or {}
            ).values()
            if isinstance(value, Mapping) and value.get("attemptId")
        )
    return attempts


def _question_attempt_ids(run):
    return list(
        dict.fromkeys(
            str(child_id)
            for execution in run.get("questionExecutions") or []
            for stage in execution.get("stages") or []
            for child_id in stage.get("childRunIds") or []
            if child_id
        )
    )


class ParallelQuestionPreparationTests(unittest.TestCase):
    def test_prepared_candidate_hash_binds_question_stage_and_inputs(self):
        candidate = _prepared_candidate_envelope(
            question_id="question-1",
            stage_id="explanation",
            input_fingerprint_value="input-1",
            projected_input_hash="projection-1",
            content={"candidatePayload": {"questionResults": []}},
        )
        self.assertEqual(
            _validated_prepared_candidate(
                candidate,
                question_id="question-1",
                stage_id="explanation",
                input_fingerprint_value="input-1",
                projected_input_hash="projection-1",
            ),
            candidate,
        )
        tampered = copy.deepcopy(candidate)
        tampered["content"]["candidatePayload"]["questionResults"].append({})
        with self.assertRaisesRegex(
            QualificationRunError,
            "content hash",
        ):
            _validated_prepared_candidate(tampered)

    def test_prepared_candidate_projection_must_still_match_saved_input_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent_run_directory = root / "workflow_runs" / "run-1"
            projected_path = (
                parent_run_directory / "projected_inputs" / "question-1.json"
            )
            projected_path.parent.mkdir(parents=True)
            projected_path.write_text('{"question_bodies":[]}\n', encoding="utf-8")
            relative_path = projected_path.relative_to(root).as_posix()
            expected_hash = hashlib.sha256(projected_path.read_bytes()).hexdigest()

            self.assertEqual(
                _validated_projected_input_path(
                    root,
                    parent_run_directory,
                    {"_projectedInputPath": relative_path},
                    expected_hash,
                ),
                projected_path.resolve(),
            )
            projected_path.write_text(
                '{"question_bodies":[{"changed":true}]}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "projected input hash",
            ):
                _validated_projected_input_path(
                    root,
                    parent_run_directory,
                    {"_projectedInputPath": relative_path},
                    expected_hash,
                )

    def test_one_hundred_questions_prepare_concurrently_and_return_in_input_order(self):
        question_ids = [f"question-{index:03d}" for index in range(100)]
        all_started = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0
        completed = []
        failure = []

        def prepare(question_id):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == len(question_ids):
                    all_started.set()
            try:
                self.assertTrue(release.wait(5))
                return f"prepared-{question_id}"
            finally:
                with lock:
                    active -= 1

        def run():
            try:
                completed.extend(
                    prepare_question_items_concurrently(
                        question_ids,
                        prepare,
                        max_workers=100,
                    )
                )
            except BaseException as exc:  # noqa: BLE001
                failure.append(exc)

        runner = threading.Thread(target=run)
        runner.start()
        started = all_started.wait(5)
        release.set()
        runner.join(10)

        self.assertTrue(started)
        self.assertFalse(runner.is_alive())
        self.assertEqual(failure, [])
        self.assertEqual(peak, 100)
        self.assertEqual(
            completed,
            [
                (question_id, f"prepared-{question_id}")
                for question_id in question_ids
            ],
        )

    def test_target_resolution_cache_reuses_unchanged_file_and_reloads_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output" / "sample" / "patch.json"
            path.parent.mkdir(parents=True)
            record = {
                "sourceQuestionKey": "sample:q1",
                "reviewQuestionId": "review-q1",
                "sourceRecordRef": "source.json#0",
                "choiceTextList": ["A"],
            }
            path.write_text(
                json.dumps({"question_bodies": [record]}),
                encoding="utf-8",
            )
            binding = SourceIdentityBinding.from_mapping(record)
            cache = TargetResolutionCache()
            loader = (
                "tools.question_review_console.question_patch_proposal."
                "_load_record_payload"
            )

            with patch(
                loader,
                wraps=question_patch_proposal._load_record_payload,
            ) as load:
                prepare_question_items_concurrently(
                    [f"question-{index:03d}" for index in range(100)],
                    lambda _question_id: assert_target_resolvable(
                        root,
                        "output/sample/patch.json",
                        binding=binding,
                        aliases=set(binding.as_tuple()),
                        cache=cache,
                    ),
                    max_workers=100,
                )
                self.assertEqual(load.call_count, 1)

                path.write_text(
                    json.dumps(
                        {
                            "question_bodies": [
                                {
                                    **record,
                                    "choiceTextList": ["A", "B"],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                assert_target_resolvable(
                    root,
                    "output/sample/patch.json",
                    binding=binding,
                    aliases=set(binding.as_tuple()),
                    cache=cache,
                )

        self.assertEqual(load.call_count, 2)


class ResumeTargetAliasTests(unittest.TestCase):
    def test_restores_only_aliases_for_the_same_complete_source_identity(self):
        binding = {
            "sourceQuestionKey": "sample:q1",
            "reviewQuestionId": "review-q1",
            "sourceRecordRef": "question.json#0",
        }
        current_aliases = [
            "sample:q1",
            "review-q1",
            "question.json#0",
        ]
        plan = {
            "targetRecordBindings": [
                {
                    "uiQuestionId": "ui-q1",
                    **binding,
                    "aliases": list(current_aliases),
                }
            ],
            "progressTargets": [
                {
                    "id": "ui-q1",
                    **binding,
                    "aliases": list(current_aliases),
                }
            ],
            "targetRecordAliasGroups": [list(current_aliases)],
            "targetSourceRecordScopes": {
                "output/sample/00_source/question.json": [list(current_aliases)]
            },
            "stagePlans": [
                {
                    "targetRecordBindings": [
                        {
                            "uiQuestionId": "ui-q1",
                            **binding,
                            "aliases": list(current_aliases),
                        }
                    ],
                    "progressTargets": [
                        {
                            "id": "ui-q1",
                            **binding,
                            "aliases": list(current_aliases),
                        }
                    ],
                    "targetRecordAliasGroups": [list(current_aliases)],
                    "targetSourceRecordScopes": {
                        "output/sample/00_source/question.json": [
                            list(current_aliases)
                        ]
                    },
                }
            ],
        }
        previous = {
            "targetRecordBindings": [
                {
                    "uiQuestionId": "ui-q1",
                    **binding,
                    "aliases": [*current_aliases, "legacy-firestore-id"],
                },
                {
                    "uiQuestionId": "ui-q2",
                    "sourceQuestionKey": "sample:q2",
                    "reviewQuestionId": "review-q2",
                    "sourceRecordRef": "question.json#1",
                    "aliases": ["unrelated-legacy-id"],
                },
            ]
        }

        _restore_resume_target_aliases(plan, previous)

        self.assertIn(
            "legacy-firestore-id",
            plan["targetRecordBindings"][0]["aliases"],
        )
        self.assertIn(
            "legacy-firestore-id",
            plan["progressTargets"][0]["aliases"],
        )
        self.assertIn(
            "legacy-firestore-id",
            plan["targetSourceRecordScopes"][
                "output/sample/00_source/question.json"
            ][0],
        )
        self.assertIn(
            "legacy-firestore-id",
            plan["stagePlans"][0]["targetRecordBindings"][0]["aliases"],
        )
        self.assertNotIn(
            "unrelated-legacy-id",
            plan["targetRecordBindings"][0]["aliases"],
        )


class ManifestRuntimeCacheTests(unittest.TestCase):
    def test_restart_uses_recovery_sidecars_instead_of_historical_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            queued = store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="queued",
                prompt="recover me",
            )
            queued_path = store._manifest_path("sample", queued["runId"])
            self.assertTrue(queued_path.with_name("recovery.json").is_file())
            historical_path = (
                store.root / "sample" / "historical-broken" / "manifest.json"
            )
            historical_path.parent.mkdir(parents=True)
            historical_path.write_text("{broken historical json", encoding="utf-8")

            restarted = QualificationRunStore(root)
            restarted.recover_interrupted_runs()
            recovered = restarted.get("sample", queued["runId"])

        self.assertEqual(recovered["status"], "interrupted")
        self.assertFalse(queued_path.with_name("recovery.json").exists())

    def test_reuses_unchanged_manifest_and_invalidates_external_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            path = (
                root
                / "output"
                / "question_review_console"
                / "workflow_runs"
                / "sample"
                / "run-1"
                / "manifest.json"
            )
            QualificationRunStore._write_json(path, {"value": "first"})

            first = store._load_manifest(path)
            with patch(
                "tools.question_review_console.qualification_runs.json.loads",
                side_effect=AssertionError("unchanged manifest was reparsed"),
            ):
                cached = store._load_manifest(path)
            QualificationRunStore._write_json(
                path,
                {"value": "externally-replaced"},
            )
            replaced = store._load_manifest(path)

        self.assertEqual(first, cached)
        self.assertIsNot(first, cached)
        self.assertIsNot(first, replaced)
        self.assertEqual(replaced, {"value": "externally-replaced"})

    def test_manifest_write_refreshes_cache_and_cache_stays_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            paths = []
            for index in range(MANIFEST_CACHE_LIMIT + 1):
                path = (
                    root
                    / "output"
                    / "question_review_console"
                    / "workflow_runs"
                    / "sample"
                    / f"run-{index}"
                    / "manifest.json"
                )
                manifest = {"value": index}
                store._write_manifest(path, manifest)
                self.assertEqual(store._load_manifest(path), manifest)
                self.assertIsNot(store._load_manifest(path), manifest)
                paths.append(path)

        self.assertEqual(len(store._manifest_cache), MANIFEST_CACHE_LIMIT)
        self.assertNotIn(paths[0], store._manifest_cache)
        self.assertIn(paths[-1], store._manifest_cache)

    def test_sixty_four_new_run_manifests_reach_persistence_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            plan = FakeWorkflow().plan("sample", "law_audit")
            plan.update(parentRunId="parent-run", targetCount=1)
            barrier = threading.Barrier(64)
            original_write = store._write_manifest

            def synchronized_write(path, manifest):
                barrier.wait(timeout=10)
                original_write(path, manifest)

            store._write_manifest = synchronized_write
            try:
                with ThreadPoolExecutor(max_workers=64) as executor:
                    runs = list(
                        executor.map(
                            lambda _index: store.create(
                                plan,
                                status="queued",
                            ),
                            range(64),
                        )
                    )
            finally:
                store._write_manifest = original_write

        self.assertEqual(len({run["runId"] for run in runs}), 64)

    def test_sixty_four_aggregate_checkpoints_persist_as_concurrent_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            plan = FakeWorkflow().plan("sample", "law_audit")
            plan.update(targetCount=64)
            parent = store.create(plan, status="running")
            parent_path = store._manifest_path("sample", parent["runId"])
            question_ids = [f"question-{index:02d}" for index in range(64)]
            barrier = threading.Barrier(64)
            original_write = store._write_aggregate_checkpoint_sidecar

            def synchronized_write(*args):
                barrier.wait(timeout=10)
                original_write(*args)

            def reserve(question_id):
                return store.reserve_aggregate_review_slot(
                    "sample",
                    parent["runId"],
                    question_id,
                    {
                        "sourceHash": f"sha256:{question_id}",
                        "model": "gpt-5.5",
                        "reasoningEffort": "high",
                    },
                    1,
                )

            store._write_aggregate_checkpoint_sidecar = synchronized_write
            try:
                with ThreadPoolExecutor(max_workers=64) as executor:
                    results = list(executor.map(reserve, question_ids))
            finally:
                store._write_aggregate_checkpoint_sidecar = original_write

            projected = store.get("sample", parent["runId"])
            raw_parent = json.loads(parent_path.read_text(encoding="utf-8"))

        self.assertTrue(all(result["status"] == "reserved" for result in results))
        self.assertEqual(
            set(projected["aggregateReviewCheckpoints"]),
            set(question_ids),
        )
        self.assertNotIn("aggregateReviewCheckpoints", raw_parent)

    def test_run_list_reuses_persistent_summary_without_parsing_heavy_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            path = (
                store.root
                / "sample"
                / "run-1"
                / "manifest.json"
            )
            store._write_manifest(
                path,
                {
                    "runId": "run-1",
                    "qualification": "sample",
                    "parentRunId": None,
                    "kind": "orchestration",
                    "workType": "maintenance_flow",
                    "status": "interrupted",
                    "updatedAt": "2026-01-01T00:00:00+09:00",
                    "questionExecutions": [
                        {"questionId": f"q{index}", "status": "queued"}
                        for index in range(200)
                    ],
                    "targetRecordScopes": {
                        f"output/q{index}.json": [[f"q{index}"]]
                        for index in range(200)
                    },
                },
            )
            sidecar = path.with_name("list_summary.json")

            restarted = QualificationRunStore(root)
            with patch.object(
                restarted,
                "_manifest_value",
                side_effect=AssertionError("heavy manifest was parsed"),
            ):
                runs = restarted.list(
                    "sample",
                    limit=8,
                    top_level_only=True,
                    newest_updated_first=True,
                    summary_only=True,
                )
            sidecar_exists = sidecar.is_file()

        self.assertTrue(sidecar_exists)
        self.assertEqual([run["runId"] for run in runs], ["run-1"])
        self.assertNotIn("questionExecutions", runs[0])
        self.assertNotIn("targetRecordScopes", runs[0])

    def test_dashboard_run_index_avoids_rescanning_historical_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            for run_id, updated_at, parent_run_id in (
                ("run-1", "2026-01-01T00:00:00+09:00", None),
                ("run-1-child", "2026-01-03T00:00:00+09:00", "run-1"),
                ("run-2", "2026-01-02T00:00:00+09:00", None),
            ):
                store._write_manifest(
                    store.root / "sample" / run_id / "manifest.json",
                    {
                        "runId": run_id,
                        "qualification": "sample",
                        "parentRunId": parent_run_id,
                        "kind": "orchestration",
                        "workType": "maintenance_flow",
                        "status": "succeeded",
                        "updatedAt": updated_at,
                    },
                )
            store.dashboard_runs("sample")

            restarted = QualificationRunStore(root)
            with patch.object(
                restarted,
                "list",
                side_effect=AssertionError("historical manifests were rescanned"),
            ):
                runs = restarted.dashboard_runs("sample")

        self.assertEqual(
            [run["runId"] for run in runs],
            ["run-2", "run-1"],
        )

    def test_batch_stage_update_writes_once_and_preserves_single_update_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "question_type", "remaining")
            plan.update(
                kind="orchestration",
                questionExecutions=[
                    {
                        "questionId": question_id,
                        "listGroupId": "2026",
                        "status": "queued",
                        "stages": [
                            {
                                "stageId": "question_type",
                                "status": "queued",
                            },
                            {
                                "stageId": "question_intent",
                                "status": "queued",
                            },
                        ],
                    }
                    for question_id in ("q1", "q2")
                ],
            )
            run = store.create(plan, status="queued", prompt="batch")
            with patch.object(
                store,
                "_write_manifest",
                wraps=store._write_manifest,
            ) as write_manifest:
                updated = store.update_question_stages(
                    "sample",
                    run["runId"],
                    [
                        {
                            "questionId": "q1",
                            "stageId": "question_type",
                            "validatedReceipt": {"recordedCount": 1, "id": "q1"},
                            "changes": {
                                "status": "validated",
                                "finishedAt": "done",
                            },
                        },
                        {
                            "questionId": "q2",
                            "stageId": "question_type",
                            "blockDependents": True,
                            "changes": {
                                "status": "blocked",
                                "error": "invalid candidate",
                                "finishedAt": "done",
                            },
                        },
                    ],
                )

            persisted = store.get("sample", run["runId"])

        self.assertEqual(write_manifest.call_count, 1)
        self.assertEqual(
            updated["questionExecutions"],
            persisted["questionExecutions"],
        )
        self.assertEqual(updated["validatedWorkItemCount"], 1)
        self.assertEqual(updated["blockedQuestionCount"], 1)
        self.assertEqual(updated["blockedWorkItemCount"], 2)
        self.assertEqual(updated["confirmedGroupIds"], ["2026"])
        self.assertEqual(updated["workVersionReceipt"]["recordedCount"], 1)
        self.assertEqual(
            updated["questionExecutions"][1]["stages"][1]["status"],
            "blocked",
        )

    def test_two_question_flow_streams_projection_updates_and_uses_one_turn_per_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "new-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            for number in (1, 2):
                question_id = f"new-exam-2026-q{number}"
                QualificationRunStore._write_json(
                    source_dir / f"question_2026_{number}.json",
                    {
                        "originalQuestionId": question_id,
                        "questionBodyText": f"記述{number}は正しい。",
                        "choiceTextList": ["正しい"],
                    },
                )
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, TwoQuestionSourceInventory()),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=PerQuestionQueueAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "group_refresh",
                stage_ids=["question_type", "question_intent"],
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["requestedQuestionIds"], [])
            self.assertEqual(
                preview["targetIdentity"]["questionIds"],
                ["new-exam-2026-q1", "new-exam-2026-q2"],
            )
            hydrate_threads = []
            original_hydrate = coordinator.store._hydrate_question_run

            def track_hydrate(*args, **kwargs):
                hydrate_threads.append(threading.current_thread().name)
                return original_hydrate(*args, **kwargs)

            with patch.object(
                coordinator, "_plan", wraps=coordinator._plan
            ) as plan_builder, patch.object(
                coordinator.store,
                "update_question_stages",
                wraps=coordinator.store.update_question_stages,
            ) as update_question_stages, patch.object(
                coordinator.store,
                "_hydrate_question_run",
                side_effect=track_hydrate,
            ):
                started = coordinator.start(
                    "new-exam",
                    preview["stageId"],
                    "group_refresh",
                    preview["previewToken"],
                    stage_ids=["question_type", "question_intent"],
                    list_group_ids=["2026"],
                    hydrate_result=False,
                )
                self.assertEqual(plan_builder.call_count, 0)
                self.assertNotIn(threading.main_thread().name, hydrate_threads)
                self.assertNotIn("questionExecutions", started["run"])
                QualificationRunTestSupport()._wait_for_job(
                    jobs,
                    started["job"]["jobId"],
                )
            run = coordinator.store.get(
                "new-exam",
                started["run"]["runId"],
            )
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                run,
            )

        batches = [call.args[2] for call in update_question_stages.call_args_list]
        self.assertEqual(run["queueStatus"], "succeeded")
        self.assertEqual(run["targetIdentity"], preview["targetIdentity"])
        self.assertEqual(run["previewPlanHash"], preview["previewPlanHash"])
        self.assertEqual(run["validatedWorkItemCount"], 4)
        self.assertEqual(run["modelBatchSize"], 1)
        self.assertEqual(len(children), 4)
        self.assertEqual(run["childRunIds"], [])
        self.assertTrue(
            all(len(child["progressTargets"]) == 1 for child in children)
        )
        self.assertTrue(
            all(
                "_scopedPlan" not in target
                for child in children
                for target in child["progressTargets"]
            )
        )
        projection_updates = [
            item
            for batch in batches
            for item in batch
            if "projectedInputPath" in (item.get("changes") or {})
        ]
        self.assertEqual(
            {
                (item["questionId"], item["stageId"])
                for item in projection_updates
            },
            {
                ("new-exam-2026-q1", "question_type"),
                ("new-exam-2026-q2", "question_type"),
                ("new-exam-2026-q1", "question_intent"),
                ("new-exam-2026-q2", "question_intent"),
            },
        )
        self.assertTrue(
            all(
                child["status"] == "succeeded"
                and child["receiptValidated"] is True
                and child["candidateTransactionOpen"] is False
                for child in children
            )
        )
        validated_updates = {
            (item["questionId"], item["stageId"])
            for batch in batches
            for item in batch
            if (item.get("changes") or {}).get("status") == "validated"
        }
        self.assertEqual(
            validated_updates,
            {
                ("new-exam-2026-q1", "question_type"),
                ("new-exam-2026-q2", "question_type"),
                ("new-exam-2026-q1", "question_intent"),
                ("new-exam-2026-q2", "question_intent"),
            },
        )

    def test_preview_reuses_exact_prepared_plan_and_keeps_execution_settings_out_of_token(self):
        class CountingWorkflow(FakeWorkflow):
            def __init__(self):
                super().__init__()
                self.plan_calls = 0

            def plan(self, *args, **kwargs):
                self.plan_calls += 1
                return super().plan(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            workflow = CountingWorkflow()
            coordinator = QualificationRunCoordinator(
                Path(directory), workflow, FakeSynchronizer(), JobManager(), "secret"
            )
            first = coordinator.preview(
                "sample",
                "explanation",
                "refresh",
                question_concurrency=10,
            )
            second = coordinator.preview(
                "sample",
                "explanation",
                "refresh",
                question_concurrency=100,
            )

        self.assertEqual(workflow.plan_calls, 1)
        self.assertEqual(first["previewToken"], second["previewToken"])
        self.assertEqual(first["questionConcurrency"], 10)
        self.assertEqual(second["questionConcurrency"], 100)

    def test_concurrent_identical_preview_uses_single_flight_plan(self):
        class SlowWorkflow(FakeWorkflow):
            def __init__(self):
                super().__init__()
                self.plan_calls = 0

            def plan(self, *args, **kwargs):
                self.plan_calls += 1
                time.sleep(0.05)
                return super().plan(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            workflow = SlowWorkflow()
            coordinator = QualificationRunCoordinator(
                Path(directory), workflow, FakeSynchronizer(), JobManager(), "secret"
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                previews = list(
                    executor.map(
                        lambda _index: coordinator.preview(
                            "sample", "explanation", "refresh"
                        ),
                        range(2),
                    )
                )

        self.assertEqual(workflow.plan_calls, 1)
        self.assertEqual(
            previews[0]["previewToken"], previews[1]["previewToken"]
        )

    def test_prepared_preview_is_not_used_after_canonical_scope_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_root = root / "output" / "sample"
            canonical_root.mkdir(parents=True)
            coordinator = QualificationRunCoordinator(
                root, FakeWorkflow(), FakeSynchronizer(), JobManager(), "secret"
            )
            preview = coordinator.preview("sample", "explanation", "refresh")
            (canonical_root / "external-change.json").write_text(
                "{}\n", encoding="utf-8"
            )
            request_key = coordinator._prepared_preview_request_key(
                "sample",
                "explanation",
                "refresh",
                stage_ids=None,
                list_group_ids=None,
                update_target_ids=None,
                question_ids=None,
                resumed_from=None,
                evaluation_rework_snapshots=None,
                blocked_rework_from=None,
            )

            prepared = coordinator._take_prepared_preview(
                request_key,
                preview["previewToken"],
                source_stamp=coordinator._prepared_preview_source_stamp(
                    "sample", None
                ),
                question_concurrency=100,
                speed_mode="standard",
            )

        self.assertIsNone(prepared)


class ResumePolicyCompatibilityTests(unittest.TestCase):
    previous = {
        "stageIds": [
            "originalize",
            "question_type",
            "question_intent",
            "correct_choice",
            "law_context",
            "explanation",
            "law_audit",
            "question_set",
        ],
        "selectedUpdateTargetIds": [
            "originalize.content",
            "question_type.question_type",
            "law_context.law_classification",
            "law_context.law_grounding",
            "explanation.basic_explanation",
            "explanation.law_support",
            "law_audit.law_audit",
            "question_set.question_set",
        ],
    }
    current_stage_ids = [
        "originalize",
        "question_type",
        "question_intent",
        "correct_choice",
        "explanation",
        "question_set",
    ]
    current_update_target_ids = [
        "originalize.content",
        "question_type.question_type",
        "explanation.basic_explanation",
        "question_set.question_set",
    ]

    def test_law_disabled_plan_allows_only_law_selection_removal(self):
        plan = {
            "lawWorkflowEnabled": False,
            "selectedUpdateTargetIds": self.current_update_target_ids,
        }

        self.assertTrue(
            _resume_orchestration_selections_match(
                self.previous,
                plan,
                self.current_stage_ids,
                compare_update_targets=True,
            )
        )

    def test_law_enabled_plan_rejects_law_selection_removal(self):
        plan = {
            "lawWorkflowEnabled": True,
            "selectedUpdateTargetIds": self.current_update_target_ids,
        }

        self.assertFalse(
            _resume_orchestration_selections_match(
                self.previous,
                plan,
                self.current_stage_ids,
                compare_update_targets=True,
            )
        )

    def test_law_disabled_plan_rejects_unrelated_stage_removal(self):
        plan = {
            "lawWorkflowEnabled": False,
            "selectedUpdateTargetIds": self.current_update_target_ids,
        }

        self.assertFalse(
            _resume_orchestration_selections_match(
                self.previous,
                plan,
                [
                    stage_id
                    for stage_id in self.current_stage_ids
                    if stage_id != "question_intent"
                ],
                compare_update_targets=True,
            )
        )

    def test_law_disabled_plan_rejects_unrelated_update_target_removal(self):
        plan = {
            "lawWorkflowEnabled": False,
            "selectedUpdateTargetIds": [
                target_id
                for target_id in self.current_update_target_ids
                if target_id != "question_type.question_type"
            ],
        }

        self.assertFalse(
            _resume_orchestration_selections_match(
                self.previous,
                plan,
                self.current_stage_ids,
                compare_update_targets=True,
            )
        )


class ServerLawAuditFieldsTests(unittest.TestCase):
    def test_group_identity_comes_from_the_one_question_plan(self):
        self.assertEqual(
            _question_plan_list_group_id(
                {
                    "targetGroupIds": ["2026"],
                    "progressTargets": [
                        {
                            "id": "question-1",
                            "listGroupId": "2026",
                        }
                    ],
                }
            ),
            "2026",
        )

    def test_conflicting_group_identity_is_rejected(self):
        with self.assertRaisesRegex(
            QualificationRunError,
            "実行計画内で一致しません",
        ):
            _question_plan_list_group_id(
                {
                    "targetGroupIds": ["2025"],
                    "progressTargets": [
                        {
                            "id": "question-1",
                            "listGroupId": "2026",
                        }
                    ],
                }
            )

    def test_server_owns_reproducible_sidecar_metadata(self):
        observed_at = datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc)
        projected = {
            "questionBodyText": "問題文",
            "choiceTextList": ["記述A"],
            "correctChoiceText": ["正しい"],
        }
        candidate = {
            "auditStatus": "same_as_current",
            "reviewState": "secondary_verified",
            "examTimeDecision": ["正しい"],
            "currentLawDecision": ["正しい"],
            "lawReferences": [[{"lawId": "123AC0000000001"}]],
            "lawRevisionFacts": [
                {
                    "auditStatus": "same_as_current",
                    "reviewState": "secondary_verified",
                }
            ],
        }

        fields = _server_law_audit_fields(
            qualification="sample-exam",
            list_group_id="2026",
            run_id="run-1",
            policy_version="4.0",
            projected=projected,
            candidate_fields=candidate,
            audited_at=observed_at,
        )

        self.assertEqual(fields["qualification"], "sample-exam")
        self.assertEqual(fields["listGroupId"], "2026")
        self.assertEqual(fields["auditedAt"], "2026-07-22T05:00:00+00:00")
        self.assertEqual(fields["nextAuditDueAt"], "2027-07-22")
        self.assertEqual(fields["auditMethodVersion"], "law-audit/4.0")
        self.assertEqual(fields["auditRunId"], "run-1")
        self.assertEqual(fields["primaryAuditRunId"], "run-1:primary")
        self.assertEqual(fields["secondaryAuditRunId"], "run-1:secondary")
        self.assertIsNone(fields["tertiaryAuditRunId"])
        self.assertRegex(fields["auditInputHash"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(fields["evidenceBindingHash"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(
            fields["lawCorpusSnapshotId"],
            r"^codex-web-primary:2026-07-22:[0-9a-f]{16}$",
        )
        self.assertFalse(fields["userVisibleNoticeRequired"])
        self.assertEqual(fields["noticeReason"], "")
        self.assertEqual(fields["remainingRisk"], "")

    def test_server_assigns_tertiary_run_id_for_current_law_update(self):
        fields = _server_law_audit_fields(
            qualification="sample-exam",
            list_group_id="2026",
            run_id="run-2",
            policy_version="4.2",
            projected={
                "questionBodyText": "問題文",
                "choiceTextList": ["記述A"],
                "correctChoiceText": ["正しい"],
            },
            candidate_fields={
                "auditStatus": "updated_to_current_law",
                "reviewState": "tertiary_verified",
                "tertiaryAuditRunId": None,
                "examTimeDecision": ["正しい"],
                "currentLawDecision": ["間違い"],
                "correctChoiceText": ["間違い"],
                "lawReferences": [[{"lawId": "123AC0000000001"}]],
                "lawRevisionFacts": [
                    {
                        "auditStatus": "updated_to_current_law",
                        "reviewState": "tertiary_verified",
                    }
                ],
            },
        )

        self.assertEqual(fields["tertiaryAuditRunId"], "run-2:tertiary")
        self.assertTrue(fields["userVisibleNoticeRequired"])


def _v2_aggregate_review_result(result, prompt):
    payload = json.loads(result.final_message)
    questions = {
        str(value["questionId"]): value
        for value in _BasePerQuestionQueueAppServer._candidate_questions(prompt)
    }
    reviews = []
    for review in payload["questionReviews"]:
        question = questions[str(review["questionId"])]
        candidates = list(question.get("candidateSets") or [])
        candidate_id = (
            candidates[0]["candidateId"]
            if review.get("classification") == "target"
            and review.get("decision") == "approve"
            and candidates
            else None
        )
        reviews.append(
            {
                "questionId": review["questionId"],
                "schemaVersion": "aggregate-answer-review/v2",
                "sourceHash": review["sourceHash"],
                "classification": review["classification"],
                "candidateId": candidate_id,
                "decision": review["decision"],
                "issueCodes": review["issueCodes"],
            }
        )
    return replace(
        result,
        final_message=json.dumps(
            {
                "schemaVersion": "aggregate-answer-review-batch/v2",
                "questionReviews": reviews,
            },
            ensure_ascii=False,
        ),
    )


class FlowAppServer(_BaseFlowAppServer):
    """Keep the shared fake's aggregate execution evidence policy-accurate."""

    def run_turn(self, prompt, **kwargs):
        result = super().run_turn(prompt, **kwargs)
        if "_aggregate_review_" not in kwargs["work_type"]:
            return result
        return replace(
            _v2_aggregate_review_result(result, prompt),
            model=kwargs["model"],
            reasoning_effort=kwargs["reasoning_effort"],
        )


class PerQuestionQueueAppServer(_BasePerQuestionQueueAppServer):
    def run_turn(self, prompt, **kwargs):
        result = super().run_turn(prompt, **kwargs)
        if "_aggregate_review_" not in kwargs["work_type"]:
            return result
        return _v2_aggregate_review_result(result, prompt)


class SourceBindingAliasTests(unittest.TestCase):
    def test_existing_review_id_alias_keeps_stable_source_binding(self):
        binding = {
            "sourceQuestionKey": "sample:2026:q1",
            "reviewQuestionId": "firestore:q1-a,q1-b",
            "sourceRecordRef": "source.json#1",
            "aliases": ["legacy-ui-id"],
        }

        self.assertTrue(
            _source_binding_accepts_identity(
                binding,
                {
                    "sourceQuestionKey": "sample:2026:q1",
                    "reviewQuestionId": "legacy-ui-id",
                    "sourceRecordRef": "source.json#1",
                },
            )
        )
        self.assertFalse(
            _source_binding_accepts_identity(
                binding,
                {
                    "sourceQuestionKey": "sample:2026:q1",
                    "reviewQuestionId": "other-id",
                    "sourceRecordRef": "source.json#1",
                },
            )
        )


class StructuredCandidateStageContextTests(unittest.TestCase):
    def test_canonical_document_guidance_embeds_document_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document = root / "prompt" / "qualification_docs" / "sample" / "README.md"
            document.parent.mkdir(parents=True)
            document.write_text("資格固有の確認済みルール", encoding="utf-8")

            guidance = _canonical_document_guidance(
                root,
                ["prompt/qualification_docs/sample/README.md"],
            )

        self.assertIn("# 正本文書の内容", guidance)
        self.assertIn("## prompt/qualification_docs/sample/README.md", guidance)
        self.assertIn("資格固有の確認済みルール", guidance)

    def test_structured_candidate_prompt_contains_canonical_guidance(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        candidate_target = CandidateTarget(
            target_id="question-1:explanation",
            role="explanation",
            path="output/sample/21_explanationText_added/question.json",
            allowed_fields=("explanationText",),
        )

        prompt = _structured_candidate_prompt(
            "解説を整える。",
            [target],
            canonical_guidance="# 正本文書の内容\n\nAWSの承認済み例",
            stage_id="explanation",
            records_by_question={"question-1": {"choiceTextList": ["A"]}},
            candidate_targets_by_question={
                "question-1": (candidate_target,)
            },
            feedback_by_question={},
        )

        self.assertIn("AWSの承認済み例", prompt)
        self.assertLess(prompt.index("AWSの承認済み例"), prompt.index("# 実行対象"))

    def test_aggregate_calculation_requires_a_candidate_when_selected(self):
        with self.assertRaisesRegex(
            QualificationRunError,
            "選択されたisCalculationQuestion",
        ):
            _aggregate_calculation_flag(
                {},
                {"isCalculationQuestion": False},
                {"questionType", "isCalculationQuestion"},
            )

    def test_aggregate_calculation_preserves_current_when_not_selected(self):
        self.assertFalse(
            _aggregate_calculation_flag(
                {},
                {"isCalculationQuestion": False},
                {"questionType"},
            )
        )

    def test_aggregate_calculation_uses_independent_candidate(self):
        self.assertTrue(
            _aggregate_calculation_flag(
                {"isCalculationQuestion": True},
                {"isCalculationQuestion": False},
                {"questionType", "isCalculationQuestion"},
            )
        )

    def test_per_choice_suggestions_remove_legacy_flat_patch_fields(self):
        target = CandidateTarget(
            target_id="q1:explanation",
            role="explanation",
            path="output/sample/21_explanationText_added/q1.json",
            allowed_fields=("suggestedQuestionDetailsByChoice",),
        )

        self.assertEqual(
            _candidate_unset_fields(
                target,
                {"suggestedQuestionDetailsByChoice": []},
                (),
            ),
            ("suggestedQuestionDetails", "suggestedQuestions"),
        )

    def test_server_authoritative_set_field_cannot_also_be_unset(self):
        target = CandidateTarget(
            target_id="q1:law_audit",
            role="law_audit",
            path="output/sample/review/law_revision_audit/q1.jsonl",
            allowed_fields=("lawReferences", "reviewNotes"),
        )

        self.assertEqual(
            _candidate_unset_fields(
                target,
                {"lawReferences": [[{"lawId": "329AC0000000051"}]]},
                ("lawReferences", "reviewNotes"),
            ),
            ("reviewNotes",),
        )

    def test_stage_commit_unsets_non_owned_legacy_answer_fields(self):
        intent = CandidateTarget(
            target_id="q1:question_intent",
            role="question_intent",
            path="output/sample/15_correctChoiceText_fixed/q1.json",
            allowed_fields=("questionIntent",),
        )
        correct = CandidateTarget(
            target_id="q1:correct_choice",
            role="correct_choice",
            path="output/sample/23_correctChoiceText_fixed/q1.json",
            allowed_fields=("correctChoiceText",),
        )

        self.assertEqual(
            _candidate_unset_fields(intent, {"questionIntent": "select_correct"}, ()),
            (
                "answer_result_inferred_correct_choice_numbers",
                "answer_result_text",
                "correctChoiceText",
            ),
        )
        self.assertEqual(
            _candidate_unset_fields(correct, {"correctChoiceText": ["正しい"]}, ()),
            (
                "answer_result_inferred_correct_choice_numbers",
                "answer_result_text",
            ),
        )

    def test_question_set_context_includes_options_and_no_op_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = root / "output" / "sample" / "category" / "category.json"
            category.parent.mkdir(parents=True)
            category.write_text(
                json.dumps(
                    {
                        "questionSets": [
                            {
                                "questionSetId": "set-1",
                                "name": "基礎理論",
                                "folderId": "folder-1",
                                "description": "理論の原則を問う問題",
                                "matchingHints": ["前提条件", "基本原則"],
                                "isDeleted": False,
                            },
                            {
                                "questionSetId": "set-old",
                                "name": "旧分類",
                                "folderId": "folder-1",
                                "isDeleted": True,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = _structured_candidate_stage_context(
                root, "sample", "question_set"
            )

        self.assertEqual(
            context["allowedQuestionSets"],
            [
                {
                    "questionSetId": "set-1",
                    "name": "基礎理論",
                    "folderId": "folder-1",
                    "description": "理論の原則を問う問題",
                    "matchingHints": ["前提条件", "基本原則"],
                }
            ],
        )
        self.assertTrue(
            any("同じ結論でも" in rule and "明示" in rule for rule in context["rules"])
        )
        self.assertTrue(
            any("choiceQuestionSetIds" in rule for rule in context["rules"])
        )
        self.assertTrue(
            any("決定要因" in rule and "数" in rule for rule in context["rules"])
        )
        self.assertTrue(
            any(
                "全て覆う候補" in rule
                and "サービス名だけ" in rule
                and "同等候補" in rule
                for rule in context["rules"]
            )
        )

    def test_question_set_prompt_repeats_current_context_after_stale_feedback(self):
        target = {"id": "question-1"}
        candidate_target = CandidateTarget(
            target_id="question-1:question-set",
            role="question_set",
            path="output/sample/22_questionSetId_linked/question.json",
            allowed_fields=("questionSetId",),
        )
        context = {
            "rules": ["問題全体の明示的な決定要因を比較する。"],
            "allowedQuestionSets": [
                {
                    "questionSetId": "set-a",
                    "description": "主要な制約を全て扱う。",
                    "matchingHints": ["主要要件", "制約"],
                }
            ],
        }

        prompt = _structured_candidate_prompt(
            "分類する。",
            [target],
            stage_id="question_set",
            records_by_question={"question-1": {"questionBodyText": "設問"}},
            candidate_targets_by_question={"question-1": (candidate_target,)},
            feedback_by_question={
                "question-1": [{"reason": "以前は複数候補を同等と判断した。"}]
            },
            stage_context=context,
        )

        feedback_position = prompt.index("以前は複数候補を同等と判断した")
        repeated_position = prompt.rindex("問題全体の明示的な決定要因を比較する")
        self.assertGreater(repeated_position, feedback_position)
        repeated = prompt[repeated_position:]
        self.assertIn('"description":"主要な制約を全て扱う。"', repeated)
        self.assertIn('"matchingHints":["主要要件","制約"]', repeated)

    def test_correct_choice_prompt_exposes_allowlisted_immutable_source_record(self):
        target = {"id": "question-1"}
        candidate_target = CandidateTarget(
            target_id="question-1:correct-choice",
            role="correct_choice",
            path="output/sample/23_correctChoiceText_fixed/question.json",
            allowed_fields=("correctChoiceText",),
        )
        source = {
            "questionBodyText": "取得元の本文",
            "choiceTextList": ["選択肢1", "選択肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "answer_result_text": "正解は1です。",
            "explanation_common_prefix": "共通説明",
            "explanation_common_summary": "要約",
            "explanation_choice_snippets": ["理由1", "理由2"],
            "explanationText": "元解説",
            "referenceUrls": ["https://example.test/reference"],
            "internalOnly": "非公開",
        }
        prompt = _structured_candidate_prompt(
            "正答を判定する。",
            [target],
            stage_id="correct_choice",
            records_by_question={
                "question-1": {
                    "questionBodyText": "現在の本文",
                    "choiceTextList": ["現在1", "現在2"],
                    "correctChoiceText": ["間違い", "正しい"],
                }
            },
            candidate_targets_by_question={"question-1": (candidate_target,)},
            feedback_by_question={},
            originalization_source_by_question={"question-1": source},
        )

        payload = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(payload["currentRecord"]["questionBodyText"], "現在の本文")
        self.assertEqual(
            payload["originalizationSource"],
            {key: value for key, value in source.items() if key != "internalOnly"},
        )
        self.assertNotIn("internalOnly", payload["originalizationSource"])
        self.assertIn("元解説又は元正答をsetFieldsへ転載せず", prompt)
        self.assertIn("correctChoiceTextをsource値から自動割当しない", prompt)

    def test_law_stage_context_prioritizes_existing_binding(self):
        context = _structured_candidate_stage_context(
            Path("."), "sample", "law_audit"
        )

        self.assertTrue(
            any(
                "既存lawReferences" in rule and "先に使う" in rule
                for rule in context["rules"]
            )
        )
        self.assertTrue(
            any(
                "広域検索" in rule and "再構築" in rule
                for rule in context["rules"]
            )
        )

    def test_law_reference_plan_limits_discovery_to_missing_choices(self):
        plan = _law_reference_discovery_plan(
            {
                "isLawRelated": True,
                "choiceTextList": ["A", "B", "C"],
                "lawReferences": [
                    [
                        {
                            "scope": "choice",
                            "choiceIndex": 0,
                            "lawId": "law-1",
                            "article": "1",
                        }
                    ],
                    [],
                    [
                        {
                            "scope": "choice",
                            "choiceIndex": 2,
                            "lawId": "law-2",
                            "sourceUrl": "https://example.invalid/law-2",
                        }
                    ],
                ],
            },
            stage_id="law_audit",
        )

        self.assertEqual(plan["strategy"], "verify_linked_then_target_gaps")
        self.assertEqual(plan["linkedChoiceIndexes"], [0, 2])
        self.assertEqual(plan["missingChoiceIndexes"], [1])
        self.assertEqual(plan["linkedLocatorCount"], 2)

    def test_law_reference_plan_is_embedded_in_attempt_prompt(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        candidate_target = CandidateTarget(
            target_id="question-1:law_context",
            role="law_context",
            path="output/sample/18_law_context_prepared/question.json",
            allowed_fields=("isLawRelated", "lawReferences"),
        )
        prompt = _structured_candidate_prompt(
            "法令根拠を確認する。",
            [target],
            stage_id="law_context",
            records_by_question={
                "question-1": {
                    "isLawRelated": True,
                    "choiceTextList": ["A", "B"],
                    "lawReferences": [
                        [
                            {
                                "scope": "question",
                                "lawId": "law-1",
                                "article": "1",
                            }
                        ],
                        [],
                    ],
                }
            },
            candidate_targets_by_question={
                "question-1": (candidate_target,)
            },
            feedback_by_question={},
        )

        question = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(
            question["lawReferenceDiscoveryPlan"]["strategy"],
            "verify_linked_first",
        )
        self.assertEqual(
            question["lawReferenceDiscoveryPlan"]["missingChoiceIndexes"],
            [],
        )
        self.assertIn("setFieldsへ転載しない", prompt)

    def test_server_primary_law_evidence_is_embedded_in_attempt_prompt(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        candidate_target = CandidateTarget(
            target_id="question-1:explanation",
            role="explanation",
            path="output/sample/21_explanationText_added/question.json",
            allowed_fields=("explanationText",),
        )
        evidence = {
            "schemaVersion": "primary-law-evidence/v2",
            "status": "complete",
            "currentAsOf": "2026-07-30",
            "items": [{"comparison": "unchanged"}],
        }
        prompt = _structured_candidate_prompt(
            "解説を整える。",
            [target],
            stage_id="explanation",
            records_by_question={
                "question-1": {
                    "choiceTextList": ["A"],
                    "lawReferences": [],
                }
            },
            candidate_targets_by_question={
                "question-1": (candidate_target,)
            },
            feedback_by_question={},
            primary_law_evidence_by_question={"question-1": evidence},
        )

        question = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(question["primaryLawEvidence"], evidence)
        self.assertIn("e-Gov法令API v2", prompt)

    def test_other_stage_context_is_empty(self):
        self.assertEqual(
            _structured_candidate_stage_context(Path("."), "sample", "explanation"),
            {},
        )


class OriginalizeWriteContractTests(unittest.TestCase):
    def test_new_originalized_patch_keeps_exact_record_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            source_path = (
                "output/sample/questions_json/independent/00_source/"
                "question_independent_1.json"
            )
            patch_path = (
                "output/sample/questions_json/independent/05_originalized/"
                "question_independent_1_originalized.json"
            )
            aliases = ["sample:independent:q1", "question_independent_1.json#0"]
            plan = {
                "qualification": "sample",
                "stageId": "originalize",
                "stageIds": ["originalize"],
                "targetGroupIds": ["independent"],
                "targetRecordAliasGroups": [aliases],
                "targetSourceRecordScopes": {source_path: [aliases]},
                "sourceFiles": [source_path],
                "outputFiles": [patch_path],
            }

            coordinator._apply_plan_write_contract(plan)

        self.assertEqual(plan["allowedPatchDirs"], ["05_originalized"])
        self.assertEqual(plan["allowedPatchFiles"], [patch_path])
        self.assertEqual(plan["allowedWriteFiles"], [])
        self.assertEqual(
            plan["targetRecordScopes"],
            {patch_path: [sorted(aliases)]},
        )


class QualificationProgressObservabilityTests(QualificationRunTestSupport):

    def test_question_ids_are_bound_to_preview_token_and_resume_scope(self):
        class ScopedWorkflow(FakeWorkflow):
            def plan(self, qualification, stage_id, mode="remaining", **scope):
                plan = super().plan(qualification, stage_id, mode)
                plan["questionIds"] = list(scope.get("question_ids") or [])
                plan["scopeListGroupIds"] = list(scope.get("list_group_ids") or [])
                return plan

            def prompt(self, qualification, stage_id, mode="remaining", **scope):
                return super().prompt(qualification, stage_id, mode)

        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory), ScopedWorkflow(), FakeSynchronizer(), JobManager(), "secret"
            )
            preview = coordinator.preview(
                "sample", "explanation", "refresh", question_ids=["q2", "q1"]
            )
            with self.assertRaisesRegex(QualificationRunError, "対象が更新"):
                coordinator.start(
                    "sample",
                    "explanation",
                    "refresh",
                    preview["previewToken"],
                    question_ids=["q1", "q2"],
                )
            previous = coordinator.store.create(
                {
                    **ScopedWorkflow().plan(
                        "sample", "explanation", "refresh", question_ids=["q2", "q1"]
                    ),
                    "kind": "orchestration",
                    "stageIds": ["explanation"],
                    "questionExecutions": [],
                    "queueStatus": "failed",
                },
                status="failed",
                prompt="work",
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "現行の一問stateを持たないrun",
            ):
                coordinator._plan(
                    "sample",
                    "explanation",
                    "refresh",
                    previous["runId"],
                    question_ids=["q1", "q2"],
                )

    def test_run_manifest_preserves_partial_refresh_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "explanation", "group_refresh")
            plan.update(
                questionIds=["q2", "q1"],
                updateTargets=[
                    {
                        "id": "supplementary_questions",
                        "selectionId": "explanation.supplementary_questions",
                        "label": "補足質問と回答",
                        "fields": ["suggestedQuestionDetailsByChoice"],
                    }
                ],
                selectedUpdateTargets=[
                    {
                        "id": "supplementary_questions",
                        "selectionId": "explanation.supplementary_questions",
                        "label": "補足質問と回答",
                        "fields": ["suggestedQuestionDetailsByChoice"],
                    }
                ],
                selectedUpdateTargetIds=[
                    "explanation.supplementary_questions"
                ],
                selectedFieldsByStage={
                    "explanation": ["suggestedQuestionDetailsByChoice"]
                },
                readFieldsByStage={
                    "explanation": ["explanationText", "questionBodyText"]
                },
            )

            run = store.create(plan, status="queued", prompt="work")
            saved = store.get("sample", run["runId"])

        self.assertEqual(saved["questionIds"], ["q2", "q1"])
        self.assertEqual(
            saved["selectedUpdateTargetIds"],
            ["explanation.supplementary_questions"],
        )
        self.assertEqual(
            saved["selectedFieldsByStage"],
            {"explanation": ["suggestedQuestionDetailsByChoice"]},
        )
        self.assertEqual(
            saved["readFieldsByStage"],
            {"explanation": ["explanationText", "questionBodyText"]},
        )
        self.assertEqual(
            saved["selectedUpdateTargets"][0]["label"],
            "補足質問と回答",
        )

    def test_technical_log_is_append_only_structured_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "delivery")
            run = coordinator.store.create(plan, status="queued")

            def worker(emit):
                emit("Authorization: Bearer should-not-be-saved")
                event = {
                    "level": "error",
                    "message": "command failed: python verify.py",
                    "commandStatus": "failed",
                    "exitCode": 7,
                    "outputTail": "token=should-not-be-saved",
                    "changedPaths": ["output/sample/patch.json"],
                    "thought": "never persist this",
                }
                getattr(emit, "event")(event)
                getattr(emit, "event")(event)
                return {"ok": True}

            started = jobs.start(
                kind="test-log",
                key="test-log",
                worker=lambda emit: coordinator._run_with_technical_log(
                    "sample",
                    run["runId"],
                    emit,
                    worker,
                ),
            )
            job = self._wait_for_job(jobs, started["jobId"], timeout=2)
            log_path = root / run["technicalLogPath"]
            first_bytes = log_path.read_bytes()
            coordinator.store.append_technical_log(
                "sample", run["runId"], {"message": "last event"}
            )
            final_bytes = log_path.read_bytes()
            events = [
                json.loads(line)
                for line in final_bytes.decode("utf-8").splitlines()
            ]

        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(final_bytes.startswith(first_bytes))
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3])
        self.assertTrue(all(event["observedAt"] for event in events))
        self.assertEqual(events[1]["commandStatus"], "failed")
        self.assertEqual(events[1]["exitCode"], 7)
        self.assertEqual(events[1]["changedPaths"], ["output/sample/patch.json"])
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("should-not-be-saved", serialized)
        self.assertNotIn("thought", serialized)
        self.assertIn("<redacted sensitive content>", serialized)

    def test_technical_log_failure_does_not_fail_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="work",
            )
            emitted: list[str] = []

            with patch.object(
                coordinator.store,
                "append_technical_log",
                side_effect=OSError("read only"),
            ):
                result = coordinator._run_with_technical_log(
                    "sample",
                    run["runId"],
                    emitted.append,
                    lambda emit: (emit("working"), {"ok": True})[1],
                )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(emitted[0], "working")
        self.assertIn("整備処理は継続します", emitted[1])

    def test_child_heartbeat_updates_parent_run_and_job_activity(self):
        class HeartbeatAppServer(SuccessfulAppServer):
            def run_turn(self, prompt, **kwargs):
                kwargs["heartbeat"]()
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            touched_jobs = []
            original_touch = jobs.touch

            def observe_touch(job_id):
                touched_jobs.append(job_id)
                original_touch(job_id)

            jobs.touch = observe_touch
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=HeartbeatAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            parent_plan = {
                **FakeWorkflow().plan("sample", "law_audit"),
                "kind": "orchestration",
                "workType": "maintenance_flow",
            }
            parent = coordinator.store.create(parent_plan, status="running")
            coordinator.store.update(
                "sample", parent["runId"], heartbeatAt="stale-parent"
            )
            child_plan = {
                **FakeWorkflow().plan("sample", "law_audit"),
                "targetCount": 1,
                "workItemCount": 1,
                "parentRunId": parent["runId"],
            }
            child = coordinator.store.create(
                child_plan,
                status="queued",
                prompt="整備する。",
            )
            prompt = coordinator.store.prompt("sample", child["runId"])
            started = jobs.start(
                kind="heartbeat-test",
                key="heartbeat-test",
                worker=lambda emit: coordinator._run_with_technical_log(
                    "sample",
                    child["runId"],
                    emit,
                    lambda logged_emit: coordinator._run_human(
                        "sample",
                        child["runId"],
                        prompt,
                        "maintenance",
                        logged_emit,
                    ),
                ),
            )
            job = self._wait_for_job(jobs, started["jobId"])
            parent_after = coordinator.store.get("sample", parent["runId"])
            child_after = coordinator.store.get("sample", child["runId"])

        self.assertEqual(job["status"], "succeeded", job)
        self.assertIn(started["jobId"], touched_jobs)
        self.assertNotEqual(parent_after["heartbeatAt"], "stale-parent")
        self.assertTrue(child_after["heartbeatAt"])
        self.assertTrue(job["lastActivityAt"])

    def test_human_run_records_validated_question_level_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["correct_choice", "explanation"],
                "stageCode": "02a → 03",
                "stageLabel": "複数工程",
                "mode": "outdated",
                "modeLabel": "洗い替え必要のみ",
                "kind": "human",
                "targetCount": 2,
                "workItemCount": 3,
                "targetGroupIds": ["2026"],
                "targetQuestionKeys": ["sample:2026:q01", "sample:2026:q02"],
                "policyTargets": {
                    "correct_choice": ["ui-q1"],
                    "explanation": ["ui-q1", "ui-q2"],
                },
                "progressTargets": [
                    {
                        "id": "ui-q1",
                        "reviewKey": "sample:2026:question_1:source-q1",
                        "questionKey": "sample:2026:q01",
                        "listGroupId": "2026",
                        "questionLabel": "問1",
                        "bodyPreview": "問題本文1",
                        "aliases": ["source-q1"],
                    },
                    {
                        "id": "ui-q2",
                        "reviewKey": "sample:2026:question_2:source-q2",
                        "questionKey": "sample:2026:q02",
                        "listGroupId": "2026",
                        "questionLabel": "問2",
                        "bodyPreview": "問題本文2",
                        "aliases": ["source-q2"],
                    },
                ],
                "stagePlans": [
                    {
                        "stageId": "correct_choice",
                        "stageCode": "02a",
                        "stageLabel": "正答精査",
                    },
                    {
                        "stageId": "explanation",
                        "stageCode": "03",
                        "stageLabel": "解説整備",
                    },
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            saved_run = store.get("sample", run["runId"])
            progress_path = root / run["progressReceiptPath"]
            progress_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {"event": "question_started", "questionId": "ui-q1"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "event": "stage_completed",
                                "questionId": "ui-q1",
                                "stageId": "correct_choice",
                                "result": {
                                    "correctChoiceText": ["正しい", "誤り"],
                                    "privateReasoning": "表示してはいけない",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "question_completed", "questionId": "ui-q1"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "event": "stage_completed",
                                "questionId": "ui-q2",
                                "stageId": "correct_choice",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "stage_completed", "questionId": "scope外", "stageId": "explanation"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "question_started", "questionId": "source-q2"},
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            store.update(
                "sample",
                run["runId"],
                status="succeeded",
                receiptValidated=True,
            )
            progress = store.progress("sample", run["runId"])
            prompt = store.prompt("sample", run["runId"])

        self.assertEqual(progress["completedQuestionCount"], 0)
        self.assertEqual(
            saved_run["progressTargets"][0]["reviewKey"],
            "sample:2026:question_1:source-q1",
        )
        self.assertEqual(progress["touchedQuestionCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)
        self.assertEqual(progress["completedWorkItemCount"], 1)
        self.assertEqual(progress["percent"], 0)
        self.assertEqual(progress["current"]["questionId"], "ui-q1")
        self.assertEqual(progress["groups"][0]["percent"], 0)
        self.assertEqual(progress["invalidEventCount"], 4)
        self.assertNotIn("privateReasoning", progress["events"][1]["result"])
        self.assertEqual(len(progress["questions"]), 1)
        self.assertFalse(progress["questions"][0]["completed"])
        self.assertEqual(len(progress["questions"][0]["outputs"]), 1)
        self.assertIn("画面用の問題別進捗", prompt)
        self.assertIn("progressTargets", prompt)
        self.assertIn("policyTargets", prompt)

    def test_qualification_scope_run_omits_question_progress_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            plan = {
                "qualification": "sample",
                "stageId": "category_setup",
                "stageCode": "03c",
                "stageLabel": "カテゴリ設計",
                "mode": "refresh",
                "modeLabel": "全体を再整備",
                "kind": "human",
                "targetCount": 1,
                "workItemCount": 1,
                "targetGroupIds": ["2026"],
                "progressTargets": [],
                "stagePlans": [
                    {
                        "stageId": "category_setup",
                        "stageCode": "03c",
                        "stageLabel": "カテゴリ設計",
                    }
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }

            run = store.create(plan, status="running", prompt="分類を整備する。")
            prompt = store.prompt("sample", run["runId"])

        self.assertNotIn("画面用の問題別進捗", prompt)
        self.assertNotIn("progressTargetsとprogressStages", prompt)
        self.assertIn("## 完了記録", prompt)

    def test_new_run_rejects_ambiguous_policy_target(self):
        plan = FakeWorkflow().plan("sample", "explanation", "outdated")
        plan.update(
            {
                "targetCount": 2,
                "workItemCount": 2,
                "policyTargets": {"explanation": ["shared-source-key"]},
                "progressTargets": [
                    {
                        "id": f"ui-q{number}",
                        "aliases": ["shared-source-key"],
                    }
                    for number in (1, 2)
                ],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            with self.assertRaisesRegex(
                QualificationRunError,
                "実行対象ID契約が不正",
            ):
                store.create(plan, status="running", prompt="work")

    def test_progress_rejects_alias_policy_contract(self):
        manifest = {
            "runId": "stored-run",
            "status": "running",
            "targetCount": 2,
            "workItemCount": 2,
            "progressStages": [
                {"id": "explanation", "code": "03", "label": "解説"}
            ],
            "progressTargets": [
                {
                    "id": f"ui-q{number}",
                    "uiQuestionId": f"ui-q{number}",
                    "questionKey": "shared-source-key",
                    "aliases": ["shared-source-key"],
                    "listGroupId": "2026",
                }
                for number in (1, 2)
            ],
            "policyTargets": {"explanation": ["shared-source-key"]},
        }

        progress = QualificationRunStore._parsed_progress(manifest, b"\n")

        self.assertEqual(progress["invalidEventCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)

    def test_progress_rejects_unique_alias_policy_target(self):
        manifest = {
            "runId": "stored-run",
            "status": "running",
            "targetCount": 1,
            "workItemCount": 1,
            "progressStages": [
                {"id": "explanation", "code": "03", "label": "解説"}
            ],
            "progressTargets": [
                {
                    "id": "ui-q1",
                    "uiQuestionId": "ui-q1",
                    "questionKey": "source-key",
                    "aliases": ["source-key"],
                    "listGroupId": "2026",
                }
            ],
            "policyTargets": {"explanation": ["source-key"]},
        }
        events = [
            {"event": "question_started", "questionId": "ui-q1"},
            {
                "event": "stage_completed",
                "questionId": "ui-q1",
                "stageId": "explanation",
            },
            {"event": "question_completed", "questionId": "ui-q1"},
        ]
        raw = "".join(json.dumps(event) + "\n" for event in events).encode()

        progress = QualificationRunStore._parsed_progress(manifest, raw)

        self.assertEqual(progress["invalidEventCount"], 2)
        self.assertEqual(progress["processedQuestionCount"], 1)
        self.assertEqual(progress["validatedQuestionCount"], 0)

    def test_progress_rejects_out_of_order_and_duplicate_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["first", "second", "third"],
                "stageCode": "01 → 02 → 03",
                "stageLabel": "複数工程",
                "mode": "outdated",
                "modeLabel": "未整備のみ",
                "kind": "human",
                "targetCount": 1,
                "workItemCount": 3,
                "targetGroupIds": ["2026"],
                "policyTargets": {
                    "first": ["q1"],
                    "second": ["q1"],
                },
                "progressTargets": [
                    {
                        "id": "q1",
                        "questionKey": "sample:2026:q1",
                        "listGroupId": "2026",
                    }
                ],
                "stagePlans": [
                    {"stageId": "first", "stageCode": "01", "stageLabel": "第一"},
                    {"stageId": "second", "stageCode": "02", "stageLabel": "第二"},
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            raw_events = [
                {"event": "stage_completed", "questionId": "q1", "stageId": "first"},
                {"event": "question_started", "questionId": "q1"},
                {"event": "question_started", "questionId": "q1"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "second"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "first"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "first"},
                {"event": "question_completed", "questionId": "q1"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "second"},
                {"event": "question_completed", "questionId": "q1"},
                {"event": "question_completed", "questionId": "q1"},
            ]
            progress_path.write_text(
                "".join(
                    json.dumps(event, ensure_ascii=False) + "\n"
                    for event in raw_events
                ),
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])

        self.assertEqual(progress["invalidEventCount"], 6)
        self.assertEqual(progress["processedWorkItemCount"], 2)
        self.assertEqual(progress["processedQuestionCount"], 1)
        self.assertEqual(
            [event["event"] for event in progress["events"]],
            [
                "question_started",
                "stage_completed",
                "stage_completed",
                "question_completed",
            ],
        )

    def test_combined_progress_separates_processed_and_validated_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            base_target = {
                "id": "q1",
                "questionKey": "sample:2026:q1",
                "listGroupId": "2026",
            }
            parent_plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["first", "second"],
                "stageCode": "01 → 02",
                "stageLabel": "トップ整備",
                "mode": "outdated",
                "modeLabel": "未整備のみ",
                "kind": "orchestration",
                "workType": "maintenance_flow",
                "targetCount": 1,
                "workItemCount": 2,
                "targetGroupIds": ["2026"],
                "policyTargets": {
                    "first": ["q1"],
                    "second": ["q1"],
                    "third": ["q1"],
                },
                "progressTargets": [base_target],
                "stagePlans": [
                    {"stageId": "first", "stageCode": "01", "stageLabel": "第一"},
                    {"stageId": "second", "stageCode": "02", "stageLabel": "第二"},
                    {"stageId": "third", "stageCode": "03", "stageLabel": "第三"},
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            parent = store.create(parent_plan, status="running")
            child_ids = []
            for stage_id, status, validated in (
                ("first", "succeeded", True),
                ("second", "failed", False),
            ):
                child_plan = {
                    **parent_plan,
                    "stageId": stage_id,
                    "stageIds": [stage_id],
                    "stageCode": "01" if stage_id == "first" else "02",
                    "stageLabel": stage_id,
                    "kind": "human",
                    "workType": f"maintenance_{stage_id}",
                    "parentRunId": parent["runId"],
                    "workItemCount": 1,
                    "policyTargets": {stage_id: ["q1"]},
                    "stagePlans": [
                        {
                            "stageId": stage_id,
                            "stageCode": "01" if stage_id == "first" else "02",
                            "stageLabel": stage_id,
                        }
                    ],
                }
                child = store.create(
                    child_plan,
                    status="running",
                    prompt="整備する。",
                )
                child_ids.append(child["runId"])
                progress_path = root / child["progressReceiptPath"]
                progress_path.write_text(
                    "".join(
                        json.dumps(event, ensure_ascii=False) + "\n"
                        for event in (
                            {"event": "question_started", "questionId": "q1"},
                            {
                                "event": "stage_completed",
                                "questionId": "q1",
                                "stageId": stage_id,
                            },
                            {"event": "question_completed", "questionId": "q1"},
                        )
                    ),
                    encoding="utf-8",
                )
                store.update(
                    "sample",
                    child["runId"],
                    status=status,
                    receiptValidated=validated,
                )
            store.update(
                "sample",
                parent["runId"],
                status="failed",
                childRunIds=child_ids,
            )

            progress = store.combined_progress("sample", parent["runId"])

        self.assertEqual(progress["processedWorkItemCount"], 2)
        self.assertEqual(progress["validatedWorkItemCount"], 1)
        self.assertEqual(progress["touchedQuestionCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)
        self.assertEqual(progress["validatedQuestionCount"], 0)
        self.assertEqual(progress["completedQuestionCount"], 0)
        self.assertEqual(progress["questions"][0]["approvalState"], "failed_unapproved")
        self.assertFalse(progress["verified"])
        self.assertEqual(progress["invalidEventCount"], 0)

    def test_parent_progress_does_not_double_count_validated_then_blocked_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            targets = [
                {
                    "id": question_id,
                    "questionId": question_id,
                    "questionKey": question_id,
                    "sourceQuestionKey": f"source-{question_id}",
                    "reviewQuestionId": f"review-{question_id}",
                    "sourceRecordRef": f"source.json#{index}",
                    "listGroupId": "2026",
                }
                for index, question_id in enumerate(("q1", "q2"), start=1)
            ]
            parent_plan = {
                **FakeWorkflow().plan("sample", "question_type", "remaining"),
                "kind": "orchestration",
                "workType": "maintenance_flow",
                "targetCount": 2,
                "workItemCount": 2,
                "progressTargets": targets,
                "policyTargets": {"question_type": ["q1", "q2"]},
                "questionExecutions": [
                    {
                        **target,
                        "status": "blocked",
                        "stages": [
                            {
                                "stageId": "question_type",
                                "status": "blocked",
                            }
                        ],
                    }
                    for target in targets
                ],
            }
            parent = store.create(parent_plan, status="running")
            child_plan = {
                **FakeWorkflow().plan("sample", "question_type", "remaining"),
                "parentRunId": parent["runId"],
                "progressTargets": [targets[0]],
                "policyTargets": {"question_type": ["q1"]},
            }
            child = store.create(child_plan, status="running", prompt="work")
            (root / child["progressReceiptPath"]).write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {"event": "question_started", "questionId": "q1"},
                        {
                            "event": "stage_completed",
                            "questionId": "q1",
                            "stageId": "question_type",
                        },
                        {"event": "question_completed", "questionId": "q1"},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            store.update(
                "sample",
                child["runId"],
                status="succeeded",
                receiptValidated=True,
            )
            store.update(
                "sample",
                parent["runId"],
                status="interrupted",
                childRunIds=[child["runId"]],
                questionExecutionSummary={
                    "validatedQuestionCount": 0,
                    "blockedQuestionCount": 2,
                    "completedWorkItemCount": 0,
                    "blockedWorkItemCount": 2,
                    "pendingWorkItemCount": 0,
                },
            )

            progress = store.combined_progress("sample", parent["runId"])

        self.assertEqual(progress["processedQuestionCount"], 2)
        self.assertEqual(progress["targetQuestionCount"], 2)
        self.assertLessEqual(
            progress["processedQuestionCount"],
            progress["targetQuestionCount"],
        )

    def test_combined_progress_uses_parent_queue_position(self):
        questions = [
            {"questionId": "q2", "targetIndex": 1},
            {"questionId": "q1", "targetIndex": 1},
        ]
        executions = [
            {"questionId": "q1", "displayOrder": 1},
            {"questionId": "q2", "displayOrder": 1},
        ]

        QualificationRunStore._order_parent_questions(questions, executions)

        self.assertEqual(
            [(question["questionId"], question["targetIndex"]) for question in questions],
            [("q1", 1), ("q2", 2)],
        )

    def test_progress_summarizes_all_58_questions_beyond_recent_event_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            question_ids = [f"q{index}" for index in range(1, 59)]
            plan = {
                "qualification": "sample",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "stageCode": "03",
                "stageLabel": "解説",
                "mode": "outdated",
                "modeLabel": "洗い替え必要のみ",
                "kind": "human",
                "targetCount": 58,
                "workItemCount": 58,
                "targetGroupIds": ["2026"],
                "policyTargets": {"explanation": question_ids},
                "progressTargets": [
                    {
                        "id": question_id,
                        "questionKey": f"sample:2026:{question_id}",
                        "listGroupId": "2026",
                        "questionLabel": f"問{index}",
                        "bodyPreview": f"問題本文{index}",
                        "aliases": [],
                    }
                    for index, question_id in enumerate(question_ids, start=1)
                ],
                "stagePlans": [
                    {
                        "stageId": "explanation",
                        "stageCode": "03",
                        "stageLabel": "解説",
                    }
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            lines = []
            for index, question_id in enumerate(question_ids, start=1):
                lines.extend(
                    [
                        {"event": "question_started", "questionId": question_id},
                        {
                            "event": "stage_completed",
                            "questionId": question_id,
                            "stageId": "explanation",
                            "result": {"explanationText": f"解説{index}"},
                        },
                        {"event": "question_completed", "questionId": question_id},
                    ]
                )
            progress_path.write_text(
                "\n".join(
                    json.dumps(line, ensure_ascii=False) for line in lines
                )
                + "\n",
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])

        self.assertEqual(len(progress["events"]), 40)
        self.assertEqual(len(progress["questions"]), 58)
        self.assertEqual(progress["questions"][0]["questionLabel"], "問1")
        self.assertEqual(progress["questions"][-1]["questionLabel"], "問58")
        self.assertEqual(
            progress["questions"][-1]["outputs"][0]["result"][
                "explanationText"
            ],
            "解説58",
        )

    def test_progress_receipt_is_not_treated_as_a_maintenance_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="整備する。",
            )
            progress_relative = str(
                (root / run["progressReceiptPath"]).relative_to(root)
            )

            coordinator._validate_changed_files(
                "sample",
                run["runId"],
                coordinator.store.get("sample", run["runId"]),
                (progress_relative,),
                (progress_relative,),
            )

    def test_external_concurrent_change_is_not_attributed_to_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="整備する。",
            )
            external = [
                ".git/HEAD",
                "docs/goals/question-maintenance/state.yaml",
            ]
            coordinator.store.update(
                "sample",
                run["runId"],
                result={
                    "status": "succeeded",
                    "commands": [],
                    "changedFiles": external,
                },
            )
            current = coordinator.store.get("sample", run["runId"])

            attribution = coordinator._validate_changed_files(
                "sample",
                run["runId"],
                current,
                (),
                tuple(external),
            )

        self.assertEqual(attribution["changedFiles"], [])
        self.assertEqual(
            attribution["externalConcurrentChangedFiles"],
            external,
        )
        self.assertEqual(
            attribution["ignoredReceiptChangedFiles"],
            external,
        )

    def test_uncommitted_external_change_is_not_attributed_to_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "law_audit")
            plan["sandbox"] = "workspace-write"
            run = coordinator.store.create(
                plan, status="running", prompt="整備する。"
            )
            outside = "scripts/unrelated_work.py"
            coordinator.store.update(
                "sample",
                run["runId"],
                result={
                    "status": "succeeded",
                    "commands": [],
                    "changedFiles": [outside],
                },
            )

            attribution = coordinator._validate_changed_files(
                "sample",
                run["runId"],
                coordinator.store.get("sample", run["runId"]),
                (),
                (outside,),
            )

        self.assertEqual(attribution["changedFiles"], [])
        self.assertEqual(
            attribution["externalConcurrentChangedFiles"],
            [outside],
        )
        self.assertEqual(
            attribution["ignoredReceiptChangedFiles"],
            [outside],
        )

    def test_app_server_scope_violation_is_not_treated_as_external_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="整備する。",
            )
            outside = "docs/unsafe.md"
            coordinator.store.update(
                "sample",
                run["runId"],
                result={
                    "status": "succeeded",
                    "commands": [],
                    "changedFiles": [outside],
                },
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "App Serverが整備責務外",
            ):
                coordinator._validate_changed_files(
                    "sample",
                    run["runId"],
                    coordinator.store.get("sample", run["runId"]),
                    (outside,),
                    (outside,),
                )


class QualificationQueueSafetyRegressionTests(QualificationRunTestSupport):
    def test_saved_v3_prepared_candidate_resumes_and_commits_through_production_dispatch(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, _sync, _server, previous = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            original_persist = coordinator.store.persist_prepared_candidate
            saved_envelope = None

            def persist_v3_then_interrupt(
                qualification,
                run_id,
                candidate,
            ):
                nonlocal saved_envelope
                content = copy.deepcopy(candidate["content"])
                question_id = previous["questionExecutions"][0]["questionId"]
                content["candidatePayload"] = {
                    "schemaVersion": "question-maintenance-candidates/v3",
                    "questionResults": [
                        {
                            "questionId": question_id,
                            "status": "candidate",
                            "summary": "保存済みv3候補を再開して確定する。",
                            "updates": [
                                {
                                    "targetId": f"{question_id}:question_type",
                                    "setFields": [
                                        {
                                            "field": "questionType",
                                            "value": "flash_card",
                                        },
                                        {
                                            "field": "isCalculationQuestion",
                                            "value": False,
                                        },
                                    ],
                                    "unsetFields": [],
                                }
                            ],
                        }
                    ],
                }
                saved_envelope = _prepared_candidate_envelope(
                    question_id=question_id,
                    stage_id="question_type",
                    input_fingerprint_value=candidate["inputFingerprint"],
                    projected_input_hash=candidate["projectedInputHash"],
                    content=content,
                )
                persisted = original_persist(
                    qualification,
                    run_id,
                    saved_envelope,
                )
                raise SystemExit("simulated stop after saved v3 preparation")

            coordinator.store.persist_prepared_candidate = (
                persist_v3_then_interrupt
            )
            with self.assertRaisesRegex(
                SystemExit,
                "after saved v3 preparation",
            ):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    previous["runId"],
                    lambda _message: None,
                )
            coordinator.store.persist_prepared_candidate = original_persist
            coordinator.store.recover_interrupted_runs()
            previous = coordinator.store.get("new-exam", previous["runId"])
            previous_attempt = _question_attempts(
                coordinator.store,
                "new-exam",
                previous,
            )[0]
            saved_envelope = copy.deepcopy(previous_attempt["preparedCandidate"])
            candidate_calls_before_resume = len(
                [
                    call
                    for call in app_server.calls
                    if call[1]["work_type"]
                    == "maintenance_question_type_candidate"
                ]
            )

            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )
            resumed = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=previous["runId"],
            )["run"]
            result = coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get(
                "new-exam",
                resumed["runId"],
            )
            resumed_attempt = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )[0]
            patch_path = root / resumed_attempt["result"]["changedFiles"][0]
            patch_records = json.loads(patch_path.read_text(encoding="utf-8"))

        self.assertEqual(candidate_calls_before_resume, 1)
        self.assertEqual(
            len(
                [
                    call
                    for call in app_server.calls
                    if call[1]["work_type"]
                    == "maintenance_question_type_candidate"
                ]
            ),
            candidate_calls_before_resume,
        )
        self.assertEqual(resumed_attempt["preparedCandidate"], saved_envelope)
        self.assertEqual(
            saved_envelope["schemaVersion"],
            "question-maintenance-prepared-candidate/v1",
        )
        self.assertEqual(
            saved_envelope["content"]["candidatePayload"]["schemaVersion"],
            "question-maintenance-candidates/v3",
        )
        self.assertEqual(
            saved_envelope["content"]["candidatePayload"]["questionResults"][0][
                "updates"
            ][0]["setFields"][0]["value"],
            "flash_card",
        )
        self.assertEqual(
            resumed_attempt["preparedCandidateReusedFromAttemptId"],
            previous_attempt["runId"],
        )
        self.assertTrue(resumed_attempt["patchApplyStartedAt"])
        self.assertTrue(resumed_attempt["receiptValidated"])
        self.assertEqual(
            resumed_attempt["workVersionReceipt"]["recordedCount"],
            1,
        )
        self.assertEqual(patch_records[0]["questionType"], "flash_card")
        self.assertEqual(
            patch_records[0]["originalQuestionId"],
            "new-exam-2026-q1",
        )
        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["queueStatus"], "succeeded")
        self.assertEqual(completed["workVersionReceipt"]["recordedCount"], 1)

    def test_question_attempt_candidate_is_durable_write_once_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
            )
            question_id = "new-exam-2026-q1"
            attempt = coordinator.store.create_question_attempt(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                parent,
                "candidate prompt",
            )
            candidate = _prepared_candidate_envelope(
                question_id=question_id,
                stage_id="question_type",
                input_fingerprint_value="input-1",
                projected_input_hash="projection-1",
                content={"candidatePayload": {"questionResults": []}},
            )

            persisted = coordinator.store.persist_prepared_candidate(
                "new-exam",
                attempt["runId"],
                candidate,
            )
            readback = coordinator.store.load_prepared_candidate(
                "new-exam",
                attempt["runId"],
                input_fingerprint_value="input-1",
                projected_input_hash="projection-1",
            )
            self.assertEqual(readback, persisted)

            changed = _prepared_candidate_envelope(
                question_id=question_id,
                stage_id="question_type",
                input_fingerprint_value="input-1",
                projected_input_hash="projection-1",
                content={"candidatePayload": {"questionResults": [{}]}},
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "preparedCandidateは変更できません",
            ):
                coordinator.store.persist_prepared_candidate(
                    "new-exam",
                    attempt["runId"],
                    changed,
                )

            coordinator.store.mark_patch_apply_started(
                "new-exam",
                attempt["runId"],
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "patch反映は再実行できません",
            ):
                coordinator.store.mark_patch_apply_started(
                    "new-exam",
                    attempt["runId"],
                )

    def test_interrupted_unwritten_candidate_is_reusable_only_for_same_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
            )
            question_id = "new-exam-2026-q1"
            attempt = coordinator.store.create_question_attempt(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                parent,
                "candidate prompt",
            )
            candidate = _prepared_candidate_envelope(
                question_id=question_id,
                stage_id="question_type",
                input_fingerprint_value="input-1",
                projected_input_hash="projection-1",
                content={"candidatePayload": {"questionResults": []}},
            )
            coordinator.store.persist_prepared_candidate(
                "new-exam",
                attempt["runId"],
                candidate,
            )
            patch_started_attempt = coordinator.store.create_question_attempt(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                parent,
                "second candidate prompt",
            )
            patch_started_candidate = _prepared_candidate_envelope(
                question_id=question_id,
                stage_id="question_type",
                input_fingerprint_value="input-2",
                projected_input_hash="projection-2",
                content={"candidatePayload": {"questionResults": []}},
            )
            coordinator.store.persist_prepared_candidate(
                "new-exam",
                patch_started_attempt["runId"],
                patch_started_candidate,
            )
            coordinator.store.mark_patch_apply_started(
                "new-exam",
                patch_started_attempt["runId"],
            )
            coordinator.store.update(
                "new-exam",
                patch_started_attempt["runId"],
                status="interrupted",
                error="process stopped after patch apply started",
            )
            coordinator.store.update(
                "new-exam",
                attempt["runId"],
                status="interrupted",
                error="process stopped",
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="interrupted",
                error="process stopped",
            )

            reusable = coordinator.store.reusable_prepared_candidate(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                input_fingerprint_value="input-1",
                projected_input_hash="projection-1",
            )
            self.assertIsNotNone(reusable)
            self.assertEqual(reusable[0], attempt["runId"])
            self.assertEqual(reusable[1], candidate)
            self.assertIsNone(
                coordinator.store.reusable_prepared_candidate(
                    "new-exam",
                    parent["runId"],
                    question_id,
                    "question_type",
                    input_fingerprint_value="input-changed",
                    projected_input_hash="projection-1",
                )
            )
            self.assertIsNone(
                coordinator.store.reusable_prepared_candidate(
                    "new-exam",
                    parent["runId"],
                    question_id,
                    "question_type",
                    input_fingerprint_value="input-1",
                    projected_input_hash="projection-changed",
                )
            )
            self.assertIsNone(
                coordinator.store.reusable_prepared_candidate(
                    "new-exam",
                    parent["runId"],
                    question_id,
                    "question_type",
                    input_fingerprint_value="input-2",
                    projected_input_hash="projection-2",
                )
            )
    def test_turn_timeout_scope_survives_exception_wrapping(self):
        timeout = CodexTurnTimeoutError("turn timeout")
        wrapped = RuntimeError("candidate failed")
        wrapped.__cause__ = timeout

        self.assertIsNone(_external_provider_failure(wrapped))
        self.assertIs(_isolated_turn_timeout(wrapped), timeout)

        provider_failure = CodexAppServerError("connection failed")
        self.assertIs(
            _external_provider_failure(provider_failure),
            provider_failure,
        )
        self.assertIsNone(_isolated_turn_timeout(provider_failure))

        control_timeout = CodexControlRequestTimeoutError(
            "hooks/list timeout"
        )
        wrapped_control_timeout = RuntimeError("candidate control failed")
        wrapped_control_timeout.__cause__ = control_timeout
        self.assertIsNone(
            _external_provider_failure(wrapped_control_timeout)
        )
        self.assertIs(
            _isolated_turn_failure(wrapped_control_timeout),
            control_timeout,
        )
        self.assertIsNone(
            _isolated_turn_timeout(wrapped_control_timeout)
        )

    @staticmethod
    def _invalid_resolved_aggregate_checkpoint(question_id, source_text):
        candidates = generate_statement_candidates(source_text)
        signature = {
            "sourceHash": source_text_hash(source_text),
            "candidateSetHash": candidate_set_hash(candidates),
            "stableParentIdentity": {
                "field": "sourceQuestionKey",
                "value": question_id.replace("new-exam-2026-q", "new-exam:2026:q"),
            },
            "model": "gpt-5.5",
            "reasoningEffort": "high",
            "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
        }
        review = {"preserved": True}
        execution = {
            "reviewNumber": 1,
            "threadId": "",
            "sessionId": "session-invalid",
            "turnId": "turn-invalid",
            "model": "gpt-5.5",
            "reasoningEffort": "high",
        }
        return {
            **signature,
            "slots": {
                "1": {
                    "slot": 1,
                    "status": "resolved",
                    "review": copy.deepcopy(review),
                    "execution": copy.deepcopy(execution),
                }
            },
            "consensus": None,
        }

    def test_aggregate_review_prompt_requires_candidate_id_without_raw_offsets(self):
        source_text = "ア　　最初の項目。\n  イ　　次の項目。"
        candidate_set = generate_statement_candidates(source_text)
        prompt = _aggregate_answer_review_prompt(
            [{"id": "question-1"}],
            {
                "question-1": {
                    "questionBodyText": source_text,
                    "choiceTextList": ["アとイ", "アのみ"],
                }
            },
            {"question-1": candidate_set},
        )

        self.assertIn("serverが原文から機械生成", prompt)
        self.assertIn("candidateIdを一つだけ選ぶ", prompt)
        self.assertIn("個別の正誤判定を求める命題そのもの", prompt)
        self.assertIn("設例条件や共通前提", prompt)
        self.assertIn("並べ替える項目", prompt)
        self.assertIn("空欄へ入れる語句又は数値", prompt)
        self.assertIn("choiceTextListに受験者が選ぶ個別の命題", prompt)
        self.assertIn("最初にquestionBodyTextとchoiceTextListの役割を確認する", prompt)
        self.assertIn("「組合せ」又は「いくつ」とあってもnon_target", prompt)
        self.assertIn("candidateSetsが空又は不完全であることだけを理由にholdにしない", prompt)
        self.assertIn('"choiceTextList":["アとイ","アのみ"]', prompt)
        self.assertIn("前提や入力を含まないcandidateId", prompt)
        self.assertIn("正誤を解かず", prompt)
        self.assertIn("正しい項目だけを選ばない", prompt)
        self.assertIn(candidate_set["candidates"][0]["candidateId"], prompt)
        self.assertIn("sourceSlice", prompt)
        self.assertNotIn('"start":', prompt)
        self.assertNotIn('"end":', prompt)
        self.assertIn("ambiguous_boundary", prompt)
        self.assertIn("ambiguous_target", prompt)
        self.assertIn("missing_statement", prompt)
        self.assertNotIn("正解", prompt)
        self.assertNotIn("answer", prompt.casefold())

    def test_aggregate_review_reread_uses_immutable_source_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_relative = Path(
                "output/sample/questions_json/group/00_source/source.json"
            )
            source_path = root / source_relative
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "questionBodyText": "A  第一の記述。\nB  第二の記述。",
                                "choiceTextList": ["AとB", "Aのみ"],
                                "correctChoiceText": ["正しい", "間違い"],
                                "answer_result_text": "正解は 1 です。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            raw_target = {
                "id": "question-1",
                "listGroupId": "group",
                "reviewQuestionId": "review-1",
                "sourceQuestionKey": "sample:group:q1",
                "sourceRecordRef": "source.json#0",
            }
            current = {
                "question-1": {
                    "questionBodyText": "A  第一の記述。\nB  第二の記述。",
                    "choiceTextList": ["A  第一の記述。", "B  第二の記述。"],
                }
            }

            records = _aggregate_review_source_records(
                root,
                "sample",
                {"sourceFiles": [source_relative.as_posix()]},
                [raw_target],
                current,
            )

            self.assertEqual(
                records["question-1"]["choiceTextList"],
                ["AとB", "Aのみ"],
            )
            self.assertEqual(
                records["question-1"]["_aggregateSourceCorrectChoiceText"],
                ["正しい", "間違い"],
            )
            self.assertEqual(
                records["question-1"]["_aggregateSourceAnswerResultText"],
                "正解は 1 です。",
            )
            self.assertEqual(current["question-1"]["choiceTextList"][0], "A  第一の記述。")

    def test_downstream_prompt_adds_prompt_only_immutable_aggregate_evidence(self):
        body = "A  第一の記述。\nB  第二の記述。"
        decomposition = {
            "schemaVersion": "aggregate-answer-decomposition/v1",
            "sourceHash": source_text_hash(body),
            "classification": "target",
            "spans": [{"start": 0, "end": 10}, {"start": 11, "end": len(body)}],
            "decision": "approve",
            "issueCodes": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_relative = Path(
                "output/sample/questions_json/group/00_source/source.json"
            )
            source_path = root / source_relative
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "questionBodyText": body,
                                "choiceTextList": ["A、B", "Aのみ"],
                                "correctChoiceText": ["正しい", "間違い"],
                                "answer_result_text": "正解は 1 です。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            target = {
                "id": "question-1",
                "listGroupId": "group",
                "reviewQuestionId": "review-1",
                "sourceQuestionKey": "sample:group:q1",
                "sourceRecordRef": "source.json#0",
            }
            current = {
                "question-1": {
                    "questionBodyText": body,
                    "choiceTextList": ["A  第一の記述。", "B  第二の記述。"],
                    "aggregateAnswerDecomposition": decomposition,
                }
            }
            evidence = _aggregate_downstream_source_evidence(
                root,
                "sample",
                {"sourceFiles": [source_relative.as_posix()]},
                [target],
                current,
            )
            candidate_target = CandidateTarget(
                target_id="correct_choice.correct_answer",
                role="correct_choice",
                path="output/sample/correct.json",
                allowed_fields=("correctChoiceText",),
            )
            prompt = _structured_candidate_prompt(
                "正答を判定する。",
                [target],
                records_by_question=current,
                candidate_targets_by_question={"question-1": (candidate_target,)},
                feedback_by_question={},
                original_aggregate_evidence_by_question=evidence,
            )

        questions = PerQuestionQueueAppServer._candidate_questions(prompt)
        prompt_evidence = questions[0]["originalAggregateAnswerEvidence"]
        self.assertEqual(prompt_evidence["choiceTextList"], ["A、B", "Aのみ"])
        self.assertEqual(prompt_evidence["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(prompt_evidence["answerResultText"], "正解は 1 です。")
        self.assertEqual(
            questions[0]["candidateTargets"][0]["allowedFields"],
            ["correctChoiceText"],
        )
        self.assertIn("更新不能な参照証拠", prompt)
        self.assertIn("抽出記述へ同じ配列を転記しない", prompt)

    def test_downstream_prompt_omits_aggregate_evidence_for_ordinary_question(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        current = {
            "question-1": {
                "questionBodyText": "正しいものを選ぶ。",
                "choiceTextList": ["第一", "第二"],
            }
        }
        evidence = _aggregate_downstream_source_evidence(
            Path("."),
            "sample",
            {"sourceFiles": []},
            [target],
            current,
        )

        self.assertEqual(evidence, {})

    def test_structured_candidate_prompt_drops_obsolete_field_scope_feedback(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        candidate_target = CandidateTarget(
            target_id="question-1:originalized",
            role="originalized",
            path="output/sample/05_originalized/question.json",
            allowed_fields=("questionBodyText", "choiceTextList"),
        )
        prompt = _structured_candidate_prompt(
            "独自問題化する。",
            [target],
            records_by_question={
                "question-1": {
                    "questionBodyText": "元の問題文",
                    "choiceTextList": ["選択肢A", "選択肢B"],
                }
            },
            candidate_targets_by_question={"question-1": (candidate_target,)},
            feedback_by_question={
                "question-1": [
                    {
                        "reason": (
                            "Codex自動整備対象外fieldの追加を検出しました: "
                            "output/sample/05_originalized/question.json / "
                            "questionBodyText"
                        )
                    },
                    {"reason": "問題文と選択肢の対応を再確認してください。"},
                ]
            },
        )

        question_payload = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(
            question_payload["previousValidationFeedback"],
            [{"reason": "問題文と選択肢の対応を再確認してください。"}],
        )
        self.assertIn("現行allowedFieldsを優先する", prompt)

    def test_structured_candidate_prompt_exposes_trusted_source_answer_evidence(self):
        target = {
            "id": "question-1",
            "listGroupId": "2023",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "gas-shunin:otsu:2023:kyokyu:q17",
            "sourceRecordRef": "question_2023_2.json#22",
        }
        source_record = {
            "questionBodyText": "正しいものの組合せはどれか。",
            "choiceTextList": ["記述イ", "記述ロ"],
            "correctChoiceText": ["間違い", "正しい"],
            "answer_result_text": "正解は 3 です。",
            "sourceProvider": "gassyunin.com",
            "sourceOrigin": "gassyunin_site",
            "choiceMarkerSource": "judge",
            "markerAlignmentMode": "judge_only",
            "markerMismatchDetected": False,
            "answerResultNumbersRemapped": False,
            "judgeChoiceMarkers": ["イ", "ロ"],
            "sourceStatementCount": 2,
        }
        evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            source_record,
        )
        self.assertIsNotNone(evidence)
        self.assertEqual(
            evidence["verdictSemantics"],
            "final_correct_choice_text_for_source_text",
        )
        self.assertEqual(
            evidence["answerResultSemantics"],
            "source_combination_choice_index",
        )
        self.assertTrue(evidence["appliesToCurrentText"])
        candidate_target = CandidateTarget(
            target_id="question-1:correct_choice",
            role="correct_choice",
            path="output/sample/23_correctChoiceText_fixed/question.json",
            allowed_fields=("correctChoiceText",),
        )

        prompt = _structured_candidate_prompt(
            "正答を判定する。",
            [target],
            records_by_question={"question-1": source_record},
            candidate_targets_by_question={
                "question-1": (candidate_target,)
            },
            feedback_by_question={},
            source_answer_evidence_by_question={
                "question-1": evidence,
            },
        )

        question_payload = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(
            question_payload["sourceAnswerEvidence"]["correctChoiceText"],
            ["間違い", "正しい"],
        )
        self.assertIn("組合せ対応表がないことだけを理由にblockedにしない", prompt)
        self.assertIn("否定語やquestionIntentを使って再反転しない", prompt)
        self.assertIn("名詞句等の断片肢では本文の述語を一度だけ補った完全命題を作り", prompt)

    def test_trusted_count_evidence_exposes_all_correct_sentinel_semantics(self):
        target = {
            "id": "question-1",
            "listGroupId": "2018",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "gas-shunin:kou:2018:kyokyu:q17",
            "sourceRecordRef": "question_2018_2.json#22",
        }
        source_record = {
            "questionBodyText": (
                "次の記述のうち、誤っているものはいくつあるか"
                "(選択肢(5)はすべて正しい)。"
            ),
            "choiceTextList": ["イ", "ロ", "ハ", "ニ", "ホ"],
            "correctChoiceText": ["正しい"] * 5,
            "answer_result_text": "正解は 5 です。",
            "sourceProvider": "gassyunin.com",
            "sourceOrigin": "gassyunin_site",
            "choiceMarkerSource": "judge",
            "markerAlignmentMode": "judge_only",
            "markerMismatchDetected": False,
            "answerResultNumbersRemapped": False,
            "judgeChoiceMarkers": ["イ", "ロ", "ハ", "ニ", "ホ"],
            "sourceStatementCount": 5,
        }

        evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            source_record,
        )

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(
            evidence["answerResultSemantics"],
            "count_choice_index_with_all_correct_sentinel",
        )

    def test_trusted_source_verdict_is_final_for_exact_fragment_question_text(self):
        target = {
            "id": "45329b3c76a7c54b",
            "listGroupId": "2018",
            "reviewQuestionId": "45329b3c76a7c54b",
            "sourceQuestionKey": "gas-shunin-otsu:2018:law:q9",
            "sourceRecordRef": "question_2018_1.json#8",
        }
        source_record = {
            "questionBodyText": (
                "次のガス工作物のうち、この規定に該当しないものはどれか。"
            ),
            "choiceTextList": [
                "ガス発生設備",
                "液化ガス用貯槽",
                "導管及びガス栓",
                "整圧器",
                "昇圧供給装置",
            ],
            "correctChoiceText": [
                "間違い",
                "間違い",
                "間違い",
                "正しい",
                "間違い",
            ],
            "answer_result_text": "正解は 4 です。",
            "sourceProvider": "gassyunin.com",
            "sourceOrigin": "gassyunin_site",
            "choiceMarkerSource": "judge",
            "markerAlignmentMode": "judge_only",
            "markerMismatchDetected": False,
            "answerResultNumbersRemapped": False,
            "judgeChoiceMarkers": ["1", "2", "3", "4", "5"],
            "sourceStatementCount": 5,
        }

        evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            source_record,
        )
        self.assertIsNotNone(evidence)
        self.assertTrue(evidence["appliesToCurrentText"])
        self.assertEqual(evidence["applicationBasis"], "exact_source_text")
        self.assertEqual(
            evidence["answerResultSemantics"],
            "source_choice_index",
        )
        self.assertEqual(
            evidence["correctChoiceText"],
            source_record["correctChoiceText"],
        )

        changed_current = {
            **source_record,
            "choiceTextList": [
                *source_record["choiceTextList"][:-1],
                "変更された選択肢",
            ],
        }
        changed_evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            changed_current,
        )
        self.assertIsNotNone(changed_evidence)
        self.assertFalse(changed_evidence["appliesToCurrentText"])
        self.assertEqual(
            changed_evidence["applicationBasis"],
            "source_text_changed_without_verified_mapping",
        )

        official_correction_evidence = [
            {
                "sourceQuestionKey": target["sourceQuestionKey"],
                "sourceRecordRef": target["sourceRecordRef"],
                "changedFields": ["choiceTextList"],
                "evidence": [
                    {
                        "sourceClass": "official",
                        "locator": "公式問題冊子 9ページ 問9",
                        "contentHash": "official-content-hash",
                    }
                ],
            }
        ]
        corrected_evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            changed_current,
            official_correction_evidence,
        )
        self.assertIsNotNone(corrected_evidence)
        self.assertTrue(corrected_evidence["appliesToCurrentText"])
        self.assertEqual(
            corrected_evidence["applicationBasis"],
            "official_question_content_correction",
        )

        reordered_current = {
            **source_record,
            "choiceTextList": [
                *source_record["choiceTextList"],
                "増えた選択肢",
            ],
        }
        reordered_evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            reordered_current,
            official_correction_evidence,
        )
        self.assertIsNotNone(reordered_evidence)
        self.assertFalse(reordered_evidence["appliesToCurrentText"])

    def test_official_firestore_source_verdict_survives_verified_text_correction(
        self,
    ):
        target = {
            "id": "question-1",
            "listGroupId": "2020",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "gas-shunin:kou:2020:kyokyu:q14",
            "sourceRecordRef": "question_2020_firestore_1.json#5",
        }
        source_record = {
            "questionBodyText": "正しいものはいくつあるか。",
            "choiceTextList": ["銅の表面", "コンクリート貫通部"],
            "correctChoiceText": ["正しい", "間違い"],
            "sourceOrigin": "firestore_snapshot",
            "sourceAcquisitionMethod": "firestore_snapshot",
            "firestoreSourceQuestions": [
                {
                    "isOfficial": True,
                    "originalQuestionChoiceText": "銅の表面",
                    "correctChoiceText": "正解",
                },
                {
                    "isOfficial": True,
                    "originalQuestionChoiceText": "コンクリート貫通部",
                    "correctChoiceText": "不正解",
                },
            ],
        }
        current_record = {
            **source_record,
            "choiceTextList": ["鋼の表面", "コンクリート貫通部"],
        }
        correction_evidence = [
            {
                "sourceQuestionKey": target["sourceQuestionKey"],
                "sourceRecordRef": target["sourceRecordRef"],
                "changedFields": ["choiceTextList"],
                "evidence": [
                    {
                        "sourceClass": "official",
                        "locator": "公式問題冊子 25ページ 問14",
                        "contentHash": "official-content-hash",
                    }
                ],
            }
        ]

        evidence = _trusted_source_answer_evidence(
            source_record,
            target,
            current_record,
            correction_evidence,
        )

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(
            evidence["evidenceType"],
            "official_firestore_snapshot_statement_verdicts",
        )
        self.assertTrue(evidence["appliesToCurrentText"])
        self.assertEqual(
            evidence["applicationBasis"],
            "official_question_content_correction",
        )
        self.assertEqual(
            evidence["correctChoiceText"],
            ["正しい", "間違い"],
        )

    def test_originalize_prompt_separates_current_record_and_source_evidence(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        candidate_target = CandidateTarget(
            target_id="question-1:originalized",
            role="originalized",
            path="output/sample/05_originalized/question.json",
            allowed_fields=("questionBodyText", "choiceTextList"),
        )
        prompt = _structured_candidate_prompt(
            "独自問題化する。",
            [target],
            records_by_question={
                "question-1": {
                    "questionBodyText": "既存の独自問題文",
                    "choiceTextList": ["独自の選択肢A", "独自の選択肢B"],
                }
            },
            candidate_targets_by_question={"question-1": (candidate_target,)},
            feedback_by_question={},
            originalization_source_by_question={
                "question-1": {
                    "questionBodyText": "取得元の問題文",
                    "choiceTextList": ["取得元A", "取得元B"],
                    "correctChoiceText": ["正しい", "間違い"],
                    "explanation_common_prefix": ["取得元の解説候補"],
                    "internalOnly": "公開しない",
                }
            },
        )

        question_payload = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(
            question_payload["currentRecord"]["questionBodyText"],
            "既存の独自問題文",
        )
        self.assertEqual(
            question_payload["originalizationSource"]["questionBodyText"],
            "取得元の問題文",
        )
        self.assertEqual(
            question_payload["originalizationSource"]["explanation_common_prefix"],
            ["取得元の解説候補"],
        )
        self.assertNotIn(
            "internalOnly",
            question_payload["originalizationSource"],
        )
        self.assertIn("00_sourceの更新不能な比較証拠", prompt)
        self.assertIn(
            "originalizationSourceを基準に、currentRecordは既存の草案として比較する",
            prompt,
        )
        self.assertIn(
            "元問題の情報と流れを保つ局所的な微修正へ整え直す",
            prompt,
        )
        self.assertIn("prompt内だけの参照資料", prompt)

    def test_structured_candidate_prompt_exposes_approved_question_correction(self):
        target = {
            "id": "question-1",
            "listGroupId": "group",
            "reviewQuestionId": "review-1",
            "sourceQuestionKey": "sample:group:q1",
            "sourceRecordRef": "source.json#0",
        }
        candidate_target = CandidateTarget(
            target_id="question-1:correct-choice",
            role="correct_choice",
            path="output/sample/23_correctChoiceText_fixed/question.json",
            allowed_fields=("correctChoiceText",),
        )
        evidence = {
            "sourceQuestionKey": "sample:group:q1",
            "reviewQuestionId": "review-1",
            "sourceRecordRef": "source.json#0",
            "changedFields": ["choiceTextList"],
            "rationale": "blind review approved",
            "evidence": [
                {
                    "sourceClass": "official",
                    "locator": "official.pdf#page=1",
                }
            ],
        }
        prompt = _structured_candidate_prompt(
            "正答を判定する。",
            [target],
            records_by_question={
                "question-1": {
                    "questionBodyText": "訂正後の問題文",
                    "choiceTextList": ["訂正後A", "訂正後B"],
                }
            },
            candidate_targets_by_question={"question-1": (candidate_target,)},
            feedback_by_question={},
            question_issue_evidence_by_question={
                "question-1": (evidence,)
            },
        )

        question_payload = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        self.assertEqual(
            question_payload["questionIssueCorrectionEvidence"],
            [evidence],
        )
        self.assertIn("専用のblind reviewと公式・一次資料", prompt)
        self.assertIn("差があることだけを理由にblocked", prompt)
        self.assertIn("currentRecordの訂正文を設問として正答を独立判定", prompt)

    def test_prompt_contract_version_is_saved_and_stale_checkpoint_holds(self):
        source_text = "ア　最初の項目。\nイ　次の項目。"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_app_server = PerQuestionQueueAppServer()
            first_coordinator, _sync, _server, first_parent = (
                self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type"],
                    app_server=first_app_server,
                )
            )
            self._write_counted_sources(
                root, 1, question_body_text=source_text
            )
            first_coordinator._run_maintenance_flow(
                "new-exam", first_parent["runId"], lambda _message: None
            )
            first_completed = first_coordinator.store.get(
                "new-exam", first_parent["runId"]
            )
            question_id = first_completed["questionExecutions"][0]["questionId"]
            checkpoint = copy.deepcopy(
                first_completed["aggregateReviewCheckpoints"][question_id]
            )
            first_child = _question_attempts(
                first_coordinator.store,
                "new-exam",
                first_completed,
            )[0]

            self.assertEqual(
                checkpoint["promptContractVersion"],
                AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
            )
            self.assertEqual(
                first_child["aggregateReviewPromptContractVersion"],
                AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
            )
            self.assertEqual(
                first_child["result"]["aggregateReviewPromptContractVersion"],
                AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
            )

            checkpoint.pop("promptContractVersion")
            second_app_server = PerQuestionQueueAppServer()
            second_root = root / "fresh"
            second_coordinator, _sync, _server, second_parent = (
                self._start_deferred_flow(
                    second_root,
                    SourceOnlyInventory(),
                    ["question_type"],
                    app_server=second_app_server,
                )
            )
            self._write_counted_sources(
                second_root, 1, question_body_text=source_text
            )
            self._write_aggregate_checkpoint(
                second_coordinator.store,
                "new-exam",
                second_parent["runId"],
                question_id,
                copy.deepcopy(checkpoint),
            )

            result = second_coordinator._run_maintenance_flow(
                "new-exam", second_parent["runId"], lambda _message: None
            )
            second_completed = second_coordinator.store.get(
                "new-exam", second_parent["runId"]
            )

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(second_app_server.aggregate_review_calls, [])
        self.assertEqual(second_app_server.calls, [])
        self.assertEqual(
            second_completed["aggregateReviewCheckpoints"][question_id], checkpoint
        )
        attempts = second_completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "blocked")

    @staticmethod
    def _mark_child_succeeded(coordinator, qualification, child_run_id):
        coordinator.store.update(
            qualification,
            child_run_id,
            status="succeeded",
            receiptValidated=True,
            workVersionReceipt={"recordedCount": 1},
            result={
                "status": "succeeded",
                "summary": "一問を確定した。",
                "commands": [{"command": "test", "status": "pass"}],
                "changedFiles": [],
            },
            error=None,
        )

    @staticmethod
    def _mark_child_failed_safely(
        coordinator,
        qualification,
        child_run_id,
        *,
        summary="patch contract failed",
    ):
        coordinator.store.update(
            qualification,
            child_run_id,
            status="failed",
            receiptValidated=False,
            receiptError="server patch validation rejected the receipt",
            deltaUnknown=False,
            rollback={
                "status": "succeeded",
                "deltaUnknown": False,
                "remainingChangedFiles": [],
            },
            result={
                "status": "failed",
                "summary": summary,
                "commands": [
                    {"command": "python check_patch.py", "status": "fail"}
                ],
                "changedFiles": [],
            },
            error=summary,
        )

    def _start_deferred_flow(
        self,
        root,
        inventory,
        stage_ids,
        app_server=None,
        group_ids=None,
        question_concurrency=5,
        model_profile="codex_only",
    ):
        selected_groups = list(group_ids or ["2026"])
        for group_id in selected_groups:
            group = inventory.group("new-exam", group_id)
            for question in group.get("questions") or []:
                source_relative = Path(question["paths"]["source"])
                source_path = root / source_relative
                if source_path.exists():
                    continue
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_record = {
                    **copy.deepcopy(question.get("projected") or {}),
                    **copy.deepcopy(question.get("source") or {}),
                    "sourceQuestionKey": question.get("sourceQuestionKey"),
                    "reviewQuestionId": question.get("originalQuestionId"),
                    "sourceRecordRef": question.get("sourceRecordRef"),
                }
                source_path.write_text(
                    json.dumps(
                        {"question_bodies": [source_record]},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
        synchronizer = FakeSynchronizer()
        app_server = app_server or FlowAppServer()
        coordinator = QualificationRunCoordinator(
            root,
            QualificationWorkflow(root, inventory),
            synchronizer,
            DeferredJobs(),
            "secret",
            app_server=app_server,
        )
        preview = coordinator.preview(
            "new-exam",
            stage_ids[0],
            "outdated",
            stage_ids=stage_ids,
            list_group_ids=selected_groups,
            question_concurrency=question_concurrency,
            model_profile=model_profile,
        )
        started = coordinator.start(
            "new-exam",
            preview["stageId"],
            "outdated",
            preview["previewToken"],
            stage_ids=preview["stageIds"],
            list_group_ids=preview["scopeListGroupIds"],
            question_concurrency=preview["questionConcurrency"],
            model_profile=model_profile,
        )
        self.assertEqual(started["run"]["workType"], "maintenance_flow")
        return coordinator, synchronizer, app_server, started["run"]

    class _HybridRouteAppServer(PerQuestionQueueAppServer):
        def __init__(self, *, retryable_failures=0, nonretryable=False, invalid_first=False):
            super().__init__()
            self.retryable_failures = retryable_failures
            self.nonretryable = nonretryable
            self.invalid_first = invalid_first

        @staticmethod
        def snapshot_for(name):
            return {
                "name": name,
                "fingerprint": "fingerprint:hybrid",
                "limits": {
                    "questionParallelism": 1,
                    "llmCallConcurrency": 1,
                    "auditBatchQuestions": 5,
                    "auditBatchInputBytes": 120000,
                },
                "roles": {},
            }

        @staticmethod
        def resolve_maintenance_attempt(profile_name, attempts, *, workflow_model):
            if any(
                value.get("backendErrorCode")
                and value.get("retryable") is False
                for value in attempts
            ):
                raise ModelBackendError("nonretryable_attempt", retryable=False)
            local_count = sum(
                value.get("attemptMode") in {"local_primary", "local_retry"}
                for value in attempts
            )
            if local_count == 0:
                mode, backend, kind, model = (
                    "local_primary", "local", "openai_compatible_http", "local-primary"
                )
            elif local_count == 1:
                mode, backend, kind, model = (
                    "local_retry", "local", "openai_compatible_http", "local-retry"
                )
            else:
                mode, backend, kind, model = (
                    "codex_fallback", "codex", "codex_app_server", workflow_model
                )
            return MaintenanceAttemptRoute(
                profile_name, "fingerprint:hybrid", mode, backend, kind, model,
                mode == "codex_fallback",
            )

        def run_turn(self, prompt, **kwargs):
            route = kwargs.get("maintenance_attempt")
            if isinstance(route, MaintenanceAttemptRoute):
                if self.nonretryable and route.attempt_mode == "local_primary":
                    raise ModelBackendError("schema_mismatch", retryable=False)
                if self.retryable_failures > 0:
                    self.retryable_failures -= 1
                    raise ModelBackendError("network", retryable=True)
            result = super().run_turn(prompt, **kwargs)
            if not isinstance(route, MaintenanceAttemptRoute):
                return result
            if self.invalid_first:
                self.invalid_first = False
                return replace(result, final_message="{}", model=route.requested_model)
            return replace(result, model=route.requested_model)

    def _run_hybrid_route(self, root, app_server):
        coordinator, _sync, _server, parent = self._start_deferred_flow(
            root,
            SourceOnlyInventory(),
            ["question_type"],
            app_server=app_server,
            model_profile="local_generate_codex_audit",
            question_concurrency=1,
        )
        coordinator._run_maintenance_flow(
            "new-exam", parent["runId"], lambda _message: None
        )
        return coordinator.store.get("new-exam", parent["runId"])

    def test_hybrid_coordinator_routes_primary_retry_fallback_and_records_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self._run_hybrid_route(
                Path(directory), self._HybridRouteAppServer(retryable_failures=2)
            )
        attempts = run["questionExecutions"][0]["stages"][0]["validationAttempts"]
        self.assertEqual(
            [value["attemptMode"] for value in attempts],
            ["local_primary", "local_retry", "codex_fallback"],
        )
        self.assertEqual(
            [value["requestedModel"] for value in attempts[:2]],
            ["local-primary", "local-retry"],
        )
        self.assertTrue(attempts[2]["requestedModel"].startswith("gpt-"))
        self.assertEqual([value["localSuccess"] for value in attempts], [False] * 3)
        self.assertEqual(
            run["modelAttemptMetrics"],
            {
                "localPrimaryCount": 1,
                "localRetryCount": 1,
                "fallbackCount": 1,
                "localSuccessCount": 0,
            },
        )

    def test_hybrid_candidate_validation_failure_is_not_local_success(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self._run_hybrid_route(
                Path(directory), self._HybridRouteAppServer(invalid_first=True)
            )
        attempts = run["questionExecutions"][0]["stages"][0]["validationAttempts"]
        self.assertEqual([value["attemptMode"] for value in attempts], ["local_primary", "local_retry"])
        self.assertEqual([value["localSuccess"] for value in attempts], [False, True])
        self.assertEqual(run["modelAttemptMetrics"]["localSuccessCount"], 1)

    def test_hybrid_nonretryable_failure_stops_without_retry_or_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self._run_hybrid_route(
                Path(directory), self._HybridRouteAppServer(nonretryable=True)
            )
        attempts = run["questionExecutions"][0]["stages"][0]["validationAttempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["attemptMode"], "local_primary")
        self.assertFalse(attempts[0]["retryable"])
        self.assertFalse(attempts[0]["fallbackUsed"])
        self.assertEqual(run["modelAttemptMetrics"]["localSuccessCount"], 0)

    @staticmethod
    def _real_profile_router(codex_client):
        config = parse_model_backend_config(
            {
                "version": 1,
                "limits": {
                    "question_parallelism": 1,
                    "llm_call_concurrency": 1,
                    "audit_batch_questions": 5,
                    "audit_batch_input_bytes": 120000,
                },
                "backends": {
                    "codex": {"kind": "codex_app_server"},
                    "local": {
                        "kind": "openai_compatible_http",
                        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
                        "model": "local-primary",
                        "retry_model": "local-retry",
                    },
                },
                "profiles": {
                    "codex_only": {
                        "roles": {
                            "maintenance": {"backend": "codex"},
                            "audit": {"backend": "codex"},
                        }
                    },
                    "local_generate_codex_audit": {
                        "roles": {
                            "maintenance": {
                                "backend": "local",
                                "local_attempts_before_fallback": 2,
                                "fallback_backend": "codex",
                            },
                            "audit": {"backend": "codex"},
                        }
                    },
                },
            }
        )
        router = ProfileModelRouter(config, codex_client)

        class LocalBackend:
            provider = "OpenAI-compatible HTTP"
            configured = True

            def __init__(self):
                self.calls = 0
                self.work_types = []

            def run_turn(self, prompt, **kwargs):
                work_type = str(kwargs.get("work_type") or "")
                if work_type.endswith("_candidate") and "_aggregate_" not in work_type:
                    self.calls += 1
                self.work_types.append(work_type)
                return codex_client.run_turn(prompt, **kwargs)

        local = LocalBackend()
        router._instances["local"] = local
        return router, local

    def test_real_profile_router_inherits_parent_snapshot_and_counts_valid_local(self):
        with tempfile.TemporaryDirectory() as directory:
            codex = PerQuestionQueueAppServer()
            router, local = self._real_profile_router(codex)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                Path(directory),
                SourceOnlyInventory(),
                ["question_type"],
                app_server=router,
                model_profile="local_generate_codex_audit",
                question_concurrency=1,
            )
            coordinator._run_maintenance_flow(
                "new-exam", parent["runId"], lambda _message: None
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            attempt = run["questionExecutions"][0]["stages"][0][
                "validationAttempts"
            ][0]
            child = coordinator.store.get("new-exam", attempt["childRunId"])

        self.assertEqual(local.calls, 1)
        self.assertEqual(local.work_types, ["maintenance_question_type_candidate"])
        self.assertEqual(
            [
                kwargs["work_type"]
                for _question_id, _prompt, kwargs in codex.aggregate_review_calls
            ],
            [
                "maintenance_question_type_aggregate_review_1_audit_candidate",
                "maintenance_question_type_aggregate_review_2_audit_candidate",
            ],
        )
        self.assertTrue(
            all(
                "_aggregate_review_" in value and "_audit_" in value
                for value in (
                    kwargs["work_type"]
                    for _question_id, _prompt, kwargs
                    in codex.aggregate_review_calls
                )
            )
        )
        self.assertTrue(attempt["localSuccess"])
        self.assertEqual(run["modelAttemptMetrics"]["localSuccessCount"], 1)
        self.assertEqual(attempt["profileName"], "local_generate_codex_audit")
        self.assertEqual(
            attempt["profileFingerprint"],
            run["llmProfile"]["fingerprint"],
        )
        self.assertEqual(child["llmProfile"], run["llmProfile"])
        self.assertIsNot(child["llmProfile"], run["llmProfile"])

    @unittest.mock.patch.object(ProfileModelRouter, "resolve_maintenance_attempt")
    def test_coordinator_profile_fingerprint_mismatch_is_one_nonretryable_attempt(
        self, resolve_attempt
    ):
        with tempfile.TemporaryDirectory() as directory:
            router, local = self._real_profile_router(PerQuestionQueueAppServer())
            resolve_attempt.return_value = MaintenanceAttemptRoute(
                "local_generate_codex_audit",
                "wrong-fingerprint",
                "local_primary",
                "local",
                "openai_compatible_http",
                "local-primary",
                False,
            )
            run = self._run_hybrid_route(Path(directory), router)
        attempts = run["questionExecutions"][0]["stages"][0]["validationAttempts"]
        self.assertEqual(len(attempts), 1)
        self.assertFalse(attempts[0]["retryable"])
        self.assertEqual(attempts[0]["backendErrorCode"], "profile_fingerprint_mismatch")
        self.assertEqual(local.calls, 0)
        self.assertEqual(
            run["modelAttemptMetrics"],
            {
                "localPrimaryCount": 1,
                "localRetryCount": 0,
                "fallbackCount": 0,
                "localSuccessCount": 0,
            },
        )

    @staticmethod
    def _write_valid_category(root):
        category_path = (
            root / "output" / "new-exam" / "category" / "category.json"
        )
        category_path.parent.mkdir(parents=True, exist_ok=True)
        category_path.write_text(
            json.dumps(
                {
                    "folders": [{"folderId": "f1"}],
                    "questionSets": [{"questionSetId": "s1", "folderId": "f1"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_invalid_queue_contract_fails_before_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, synchronizer, app_server, parent = (
                self._start_deferred_flow(
                    Path(directory),
                    SourceOnlyInventory(),
                    ["question_type"],
                )
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                queueOrder=None,
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "一問queue契約が不正",
            ):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            run = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["queueStatus"], "failed")
        self.assertIn("一問queue契約が不正", run["error"])
        self.assertEqual(app_server.calls, [])
        self.assertEqual(synchronizer.calls, [])

    def test_resume_accepts_succeeded_partial_state(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                Path(directory),
                SourceOnlyInventory(),
                ["question_type"],
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="succeeded",
                queueStatus="partial",
            )

            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=parent["runId"],
            )

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(preview["workItemCount"], 1)

    def test_succeeded_partial_resume_does_not_requeue_validated_policy_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                Path(directory),
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            first, second = parent["questionExecutions"]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                first["questionId"],
                "question_type",
                status="validated",
                policyFingerprint="previous-policy",
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                second["questionId"],
                "question_type",
                status="blocked",
                error="この問題だけ再実行する。",
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="succeeded",
                queueStatus="partial",
            )

            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=parent["runId"],
            )
            resumed = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=parent["runId"],
            )["run"]

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(preview["workItemCount"], 1)
        self.assertTrue(preview["canStart"])
        self.assertEqual(preview["resumedFrom"], parent["runId"])
        self.assertEqual(resumed["resumedFrom"], parent["runId"])
        self.assertEqual(
            [question["questionId"] for question in resumed["questionExecutions"]],
            [second["questionId"]],
        )

    @staticmethod
    def _mark_parent_partial(coordinator, parent):
        question = parent["questionExecutions"][0]
        stage = question["stages"][0]
        coordinator.store.update_question_stage(
            "new-exam",
            parent["runId"],
            question["questionId"],
            stage["stageId"],
            status="blocked",
            error="再実行対象として保留した。",
            block_dependents=True,
        )
        return coordinator.store.update(
            "new-exam",
            parent["runId"],
            status="failed",
            queueStatus="partial",
        )

    @staticmethod
    def _attach_unsafe_child(coordinator, parent):
        child_plan = FakeWorkflow().plan("new-exam", "law_audit")
        child_plan.update(
            parentRunId=parent["runId"],
            stageIds=["question_type"],
            workType="maintenance_question_type",
        )
        child = coordinator.store.create(
            child_plan,
            status="failed",
            prompt="unsafe child",
        )
        child = coordinator.store.update(
            "new-exam",
            child["runId"],
            startedAt="started",
            deltaUnknown=True,
            rollback={
                "status": "failed",
                "deltaUnknown": True,
                "remainingChangedFiles": [],
            },
            result={
                "status": "failed",
                "summary": "rollback safety unknown",
                "commands": [],
                "changedFiles": [],
            },
            error="rollback safety unknown",
        )
        current = coordinator.store.get("new-exam", parent["runId"])
        coordinator.store.update(
            "new-exam",
            parent["runId"],
            childRunIds=[
                *list(current.get("childRunIds") or []),
                child["runId"],
            ],
        )
        return child

    def test_recent_is_read_only_for_retry_unsafe_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="interrupted",
                queueStatus="partial",
                retrySafe=False,
                retryUnsafeReason="未確定差分を確認できない。",
            )
            manifest_path = coordinator.store._manifest_path(
                "new-exam",
                parent["runId"],
            )
            before = manifest_path.read_bytes()

            recent = coordinator.recent("new-exam")

            after = manifest_path.read_bytes()

        self.assertFalse(recent["runs"][0]["retrySafe"])
        self.assertEqual(after, before)

    def test_recent_hides_failed_delta_reconciliation_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            visible = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="succeeded",
                prompt="visible",
            )
            receipt = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="succeeded",
                prompt="receipt",
            )
            coordinator.store.update(
                "sample",
                receipt["runId"],
                schemaVersion="failed-delta-reconciliation/v1",
            )

            recent = coordinator.recent("sample")

        self.assertEqual(
            [run["runId"] for run in recent["runs"]],
            [visible["runId"]],
        )

    def test_recent_limits_runs_after_excluding_child_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            visible = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="interrupted",
                prompt="visible parent",
            )
            child_plan = FakeWorkflow().plan("sample", "law_audit")
            child_plan["parentRunId"] = visible["runId"]
            for _ in range(101):
                coordinator.store.create(
                    child_plan,
                    status="succeeded",
                    prompt="child",
                )

            recent = coordinator.recent("sample")

        self.assertEqual(
            [run["runId"] for run in recent["runs"]],
            [visible["runId"]],
        )

    def test_recent_prefers_the_parent_run_updated_most_recently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            resumed = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="interrupted",
                prompt="resumed parent",
            )
            duplicate = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="failed",
                prompt="later duplicate",
            )
            coordinator.store.update(
                "sample",
                resumed["runId"],
                error="再開後に中断した。",
            )
            resumed_manifest = coordinator.store.get(
                "sample",
                resumed["runId"],
            )
            resumed_manifest["updatedAt"] = "2099-01-01T00:00:00+09:00"
            QualificationRunStore._write_json(
                coordinator.store._manifest_path(
                    "sample",
                    resumed["runId"],
                ),
                resumed_manifest,
            )

            recent = coordinator.recent("sample")

        self.assertEqual(
            [run["runId"] for run in recent["runs"][:2]],
            [resumed["runId"], duplicate["runId"]],
        )

    def test_recent_loads_only_the_requested_updated_parent_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            parents = [
                coordinator.store.create(
                    FakeWorkflow().plan("sample", "law_audit"),
                    status="interrupted",
                    prompt=f"parent {index}",
                )
                for index in range(10)
            ]
            loaded_paths = []
            original_load = coordinator.store._load_manifest_list_summary

            def tracked_load(path):
                loaded_paths.append(path)
                return original_load(path)

            coordinator.store._load_manifest_list_summary = tracked_load
            recent = coordinator.recent("sample")

        self.assertEqual(len(recent["runs"]), 8)
        self.assertEqual(
            [run["runId"] for run in recent["runs"]],
            [run["runId"] for run in reversed(parents[-8:])],
        )
        self.assertEqual(len(loaded_paths), 8)

    def test_unsafe_category_setup_stops_dependent_queue_and_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    LawSourceInventory(),
                    ["category_setup", "question_set"],
                )
            )
            writer_calls = []

            def fail_unsafe_scope(
                qualification,
                child_run_id,
                *_args,
                **_kwargs,
            ):
                writer_calls.append(child_run_id)
                coordinator.store.update(
                    qualification,
                    child_run_id,
                    status="failed",
                    receiptValidated=False,
                    deltaUnknown=True,
                    rollback={
                        "status": "failed",
                        "deltaUnknown": True,
                        "remainingChangedFiles": [],
                    },
                    result={
                        "status": "failed",
                        "summary": "category setup failed",
                        "commands": [],
                        "changedFiles": [],
                    },
                    error="category setup failed",
                )
                raise RuntimeError("category setup failed")

            coordinator._run_human = fail_unsafe_scope
            with patch(
                "tools.question_review_console.qualification_runs."
                "sync_after_patch_update"
            ) as artifact_sync:
                with self.assertRaisesRegex(
                    QualificationRunError,
                    "rollback完了を検証できない",
                ):
                    coordinator._run_maintenance_flow(
                        "new-exam",
                        parent["runId"],
                        lambda _message: None,
                    )

            run = coordinator.store.get("new-exam", parent["runId"])
            self.assertEqual(run["status"], "failed")
            self.assertFalse(run["retrySafe"])
            self.assertTrue((root / run["improvementReportPath"]).is_file())
            self.assertEqual(run["unsafeChildRunId"], writer_calls[0])
            self.assertEqual(len(writer_calls), 1)
            self.assertTrue(
                all(
                    stage["status"] == "blocked"
                    for question in run["questionExecutions"]
                    for stage in question["stages"]
                )
            )
            artifact_sync.assert_not_called()
            self.assertEqual(synchronizer.calls, [])

    def test_category_setup_provider_gate_pauses_parent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    LawSourceInventory(),
                    ["category_setup", "question_set"],
                )
            )
            writer_ids = []

            def gated_scope(qualification, child_run_id, *_args, **_kwargs):
                writer_ids.append(child_run_id)
                self._mark_child_failed_safely(
                    coordinator,
                    qualification,
                    child_run_id,
                    summary="利用上限を確認できません。",
                )
                try:
                    raise SubscriptionGateError("利用上限を確認できません。")
                except SubscriptionGateError as cause:
                    raise QualificationRunError("scope writerを開始できません。") from cause

            coordinator._run_human = gated_scope
            with patch(
                "tools.question_review_console.qualification_runs."
                "sync_after_patch_update"
            ) as artifact_sync:
                with self.assertRaisesRegex(QuestionQueuePaused, "利用上限"):
                    coordinator._run_maintenance_flow(
                        "new-exam",
                        parent["runId"],
                        lambda _message: None,
                    )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(len(writer_ids), 1)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertEqual(completed["pauseKind"], "external_provider")
        self.assertTrue(completed["retrySafe"])
        self.assertTrue(
            all(
                stage["status"] == "blocked"
                for question in completed["questionExecutions"]
                for stage in question["stages"]
            )
        )
        artifact_sync.assert_not_called()
        self.assertEqual(synchronizer.calls, [])

    def test_safe_category_setup_failure_blocks_only_dependent_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer(fail_on_writer=2)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    LawSourceInventory(),
                    ["question_type", "category_setup", "question_set"],
                    app_server=app_server,
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            stages = {
                stage["stageId"]: stage["status"]
                for question in run["questionExecutions"]
                for stage in question["stages"]
            }
            phase_statuses = {
                phase["id"]: phase["status"]
                for phase in run["phaseExecutions"]
            }
            self.assertEqual(result["queueStatus"], "partial")
            self.assertEqual(run["status"], "succeeded")
            self.assertTrue(run["retrySafe"])
            self.assertEqual(stages["question_type"], "validated")
            self.assertEqual(stages["question_set"], "blocked")
            self.assertEqual(phase_statuses["question_type"], "succeeded")
            self.assertEqual(phase_statuses["category_setup"], "failed")
            self.assertEqual(phase_statuses["question_set"], "partial")
            self.assertEqual(
                [kwargs["work_type"] for _prompt, kwargs in app_server.calls],
                [
                    "maintenance_question_type_candidate",
                    "maintenance_category_setup",
                ],
            )
            self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])


    @staticmethod
    def _write_counted_sources(root, count, *, question_body_text=None):
        source_dir = (
            root
            / "output/new-exam/questions_json/2026/00_source"
        )
        source_dir.mkdir(parents=True, exist_ok=True)
        for number in range(1, count + 1):
            question_id = f"new-exam-2026-q{number}"
            (source_dir / f"question_2026_{number}.json").write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "original_question_id": question_id,
                                "sourceQuestionKey": f"new-exam:2026:q{number}",
                                "reviewQuestionId": question_id,
                                "sourceRecordRef": f"question_2026_{number}.json#0",
                                **(
                                    {"questionBodyText": question_body_text}
                                    if question_body_text is not None
                                    else {}
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def test_five_questions_use_five_independent_model_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(5),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 5)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(
            sorted(app_server.batch_calls),
            sorted(
                [(f"new-exam-2026-q{number}",) for number in range(1, 6)]
            ),
        )
        self.assertEqual(
            len(_question_attempt_ids(completed)),
            5,
        )
        self.assertEqual(completed["childRunIds"], [])
        self.assertEqual(completed["validatedQuestionCount"], 5)
        self.assertEqual(completed["modelBatchSize"], 1)
        self.assertEqual(completed["modelWorkerLimit"], 5)

    def test_slow_question_preparation_does_not_block_ready_model_turn(self):
        class StreamingInventory(CountedSourceInventory):
            def __init__(self):
                super().__init__(3)
                self.model_started = threading.Event()

            def projected_input(
                self,
                qualification,
                list_group_id,
                source_record_ref,
            ):
                if (
                    source_record_ref == "question_2026_1.json#0"
                    and not self.model_started.wait(2)
                ):
                    raise AssertionError(
                        "a ready question did not start while q1 was preparing"
                    )
                return super().projected_input(
                    qualification,
                    list_group_id,
                    source_record_ref,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = StreamingInventory()
            app_server = PerQuestionQueueAppServer(
                before_receipt=lambda *_args: inventory.model_started.set()
            )
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                inventory,
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 3)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertTrue(inventory.model_started.is_set())
        self.assertEqual(
            sorted(app_server.batch_calls),
            sorted(
                [
                    ("new-exam-2026-q1",),
                    ("new-exam-2026-q2",),
                    ("new-exam-2026-q3",),
                ]
            ),
        )
        self.assertEqual(completed["preparationProgress"]["status"], "prepared")
        self.assertEqual(completed["preparationProgress"]["preparedCount"], 3)
        self.assertEqual(completed["questionWindowLimit"], 3)
        self.assertEqual(completed["questionWindowPeakPendingCount"], 3)

    def test_final_chunk_applies_fast_outcome_before_slowest_turn_finishes(self):
        class UnevenTurnAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.slow_question_id = "new-exam-2026-q1"
                self.slow_release = threading.Event()
                self.fast_returned = threading.Event()

            def run_turn(self, prompt, **kwargs):
                question_id = self._question_ids(prompt)[0]
                candidate_work = kwargs["work_type"].endswith("_candidate")
                if candidate_work and question_id == self.slow_question_id:
                    if not self.slow_release.wait(5):
                        raise AssertionError("slow turn was not released")
                result = super().run_turn(prompt, **kwargs)
                if candidate_work and question_id != self.slow_question_id:
                    self.fast_returned.set()
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = UnevenTurnAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(2),
                ["question_type"],
                app_server=app_server,
                question_concurrency=5,
            )
            self._write_counted_sources(root, 2)
            outcome: dict[str, object] = {}
            failure: list[BaseException] = []

            def run_flow():
                try:
                    outcome.update(
                        coordinator._run_maintenance_flow(
                            "new-exam",
                            parent["runId"],
                            lambda _message: None,
                        )
                    )
                except BaseException as exc:
                    failure.append(exc)

            runner = threading.Thread(target=run_flow)
            runner.start()
            try:
                self.assertTrue(app_server.fast_returned.wait(5))
                deadline = time.monotonic() + 5
                fast_status = None
                while time.monotonic() < deadline:
                    current = coordinator.store.get(
                        "new-exam",
                        parent["runId"],
                    )
                    fast_status = next(
                        question["stages"][0]["status"]
                        for question in current["questionExecutions"]
                        if question["questionKey"] == "new-exam:2026:q2"
                    )
                    if fast_status == "validated":
                        break
                    time.sleep(0.01)
                self.assertEqual(fast_status, "validated")
                self.assertTrue(runner.is_alive())
            finally:
                app_server.slow_release.set()
                runner.join(timeout=10)

            self.assertFalse(runner.is_alive())
            self.assertEqual(failure, [])
            self.assertEqual(outcome["queueStatus"], "succeeded")

    def test_fast_question_advances_before_another_question_finishes_prior_stage(self):
        class UnevenStageAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.slow_release = threading.Event()
                self.fast_next_stage_started = threading.Event()

            def run_turn(self, prompt, **kwargs):
                question_id = self._question_ids(prompt)[0]
                work_type = kwargs["work_type"]
                if (
                    question_id == "new-exam-2026-q1"
                    and work_type == "maintenance_question_type_candidate"
                    and not self.slow_release.wait(5)
                ):
                    raise AssertionError("slow question was not released")
                if (
                    question_id == "new-exam-2026-q2"
                    and work_type == "maintenance_question_intent_candidate"
                ):
                    self.fast_next_stage_started.set()
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = UnevenStageAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(2),
                ["question_type", "question_intent"],
                app_server=app_server,
                question_concurrency=5,
            )
            self._write_counted_sources(root, 2)
            outcome: dict[str, object] = {}
            failure: list[BaseException] = []

            def run_flow():
                try:
                    outcome.update(
                        coordinator._run_maintenance_flow(
                            "new-exam",
                            parent["runId"],
                            lambda _message: None,
                        )
                    )
                except BaseException as exc:
                    failure.append(exc)

            runner = threading.Thread(target=run_flow)
            runner.start()
            try:
                self.assertTrue(
                    app_server.fast_next_stage_started.wait(5),
                    "q2 did not enter its next stage while q1 was still running",
                )
                self.assertTrue(runner.is_alive())
            finally:
                app_server.slow_release.set()
                runner.join(timeout=15)

            self.assertFalse(runner.is_alive())
            self.assertEqual(failure, [])
            self.assertEqual(outcome["queueStatus"], "succeeded")

    def test_failed_question_retries_after_normal_queue_is_drained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer(
                failed_question_id="new-exam-2026-q1"
            )
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(6),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 6)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        call_counts = {
            question_id: app_server.batch_calls.count((question_id,))
            for question_id in (
                f"new-exam-2026-q{number}" for number in range(1, 7)
            )
        }
        self.assertEqual(call_counts["new-exam-2026-q1"], 3)
        self.assertTrue(
            all(
                call_counts[f"new-exam-2026-q{number}"] == 1
                for number in range(2, 7)
            )
        )
        self.assertEqual(completed["validatedQuestionCount"], 5)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        models_by_question = {
            question_id: [
                kwargs["model"]
                for value, _prompt, kwargs in app_server.calls
                if value == question_id
            ]
            for question_id in call_counts
        }
        self.assertEqual(
            sorted(models_by_question["new-exam-2026-q1"]),
            ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-sol"],
        )
        self.assertTrue(
            all(
                models_by_question[f"new-exam-2026-q{number}"] == ["gpt-5.6-luna"]
                for number in range(2, 7)
            )
        )
        self.assertEqual(len(app_server.aggregate_review_calls), 12)
        self.assertTrue(
            all(
                kwargs["model"] == "gpt-5.6-luna"
                and kwargs["reasoning_effort"] == "high"
                for _question_id, _prompt, kwargs in app_server.aggregate_review_calls
            )
        )
        failed_stage = completed["questionExecutions"][0]["stages"][0]
        checkpoint = completed["aggregateReviewCheckpoints"][
            "new-exam-2026-q1"
        ]
        self.assertEqual(len(checkpoint["slots"]), 2)
        self.assertNotIn("reviews", checkpoint)
        self.assertNotIn("executions", checkpoint)
        self.assertEqual(
            [attempt["requestedModel"] for attempt in failed_stage["validationAttempts"]],
            ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-sol"],
        )
        self.assertTrue(
            all(
                attempt["requestedReasoningEffort"] == "high"
                and attempt["reasoningEffort"] == "high"
                for attempt in failed_stage["validationAttempts"]
            )
        )
        successful_stage = completed["questionExecutions"][1]["stages"][0]
        self.assertEqual(len(successful_stage["validationAttempts"]), 1)
        self.assertEqual(
            successful_stage["validationAttempts"][0]["requestedModel"],
            "gpt-5.6-luna",
        )

    def test_review_checkpoint_mismatch_blocks_without_third_review(self):
        class MismatchingCheckpointAppServer(PerQuestionQueueAppServer):
            coordinator = None
            parent_run_id = ""
            checkpoint_changed = False

            def run_turn(self, prompt, **kwargs):
                result = super().run_turn(prompt, **kwargs)
                if (
                    kwargs["work_type"] == "maintenance_question_type_candidate"
                    and not self.checkpoint_changed
                ):
                    parent = self.coordinator.store.get(
                        "new-exam",
                        self.parent_run_id,
                    )
                    checkpoint = copy.deepcopy(
                        parent["aggregateReviewCheckpoints"][
                            "new-exam-2026-q1"
                        ]
                    )
                    checkpoint["sourceHash"] = (
                        "sha256:" + "0" * 64
                    )
                    QualificationRunTestSupport._write_aggregate_checkpoint(
                        self.coordinator.store,
                        "new-exam",
                        self.parent_run_id,
                        "new-exam-2026-q1",
                        checkpoint,
                    )
                    self.checkpoint_changed = True
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = MismatchingCheckpointAppServer(
                failed_question_id="new-exam-2026-q1"
            )
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            app_server.coordinator = coordinator
            app_server.parent_run_id = parent["runId"]
            self._write_counted_sources(root, 1)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(app_server.aggregate_review_calls), 2)
        attempts = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual([value["status"] for value in attempts], ["failed", "blocked"])
        self.assertEqual(
            attempts[-1]["feedback"]["issues"][0]["code"],
            "aggregate_review_hold",
        )

    def test_review_disagreement_is_adjudicated_once_and_can_be_accepted(self):
        question_id = "new-exam-2026-q1"
        source_text = "A　最初の記述。\nB　次の記述。"
        app_server = PerQuestionQueueAppServer(
            aggregate_review_overrides={
                (question_id, 1): {
                    "classification": "target",
                    "decision": "approve",
                },
                (question_id, 2): {
                    "classification": "non_target",
                    "decision": "approve",
                },
                (question_id, 3): {
                    "classification": "non_target",
                    "decision": "approve",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(
                root,
                1,
                question_body_text=source_text,
            )

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            child = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )[0]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(len(app_server.aggregate_review_calls), 3)
        self.assertEqual(
            [
                kwargs["work_type"]
                for _question_id, _prompt, kwargs
                in app_server.aggregate_review_calls
            ],
            [
                "maintenance_question_type_aggregate_review_1_audit_candidate",
                "maintenance_question_type_aggregate_review_2_audit_candidate",
                "maintenance_question_type_aggregate_review_3_audit_adjudication",
            ],
        )
        checkpoint = completed["aggregateReviewCheckpoints"][question_id]
        self.assertEqual(
            checkpoint["consensus"]["issueCodes"],
            ["review_disagreement"],
        )
        self.assertEqual(child["aggregateReviewAdjudicatedCount"], 1)
        self.assertEqual(
            child["aggregateReviewAdjudicatedQuestionIds"],
            [question_id],
        )
        self.assertEqual(len(child["aggregateReviewExecutions"]), 3)
        self.assertEqual(
            child["aggregateReviewExecutions"][-1]["role"],
            "adjudication",
        )
        question_result = child["batchQuestionResults"][0]
        self.assertEqual(question_result["status"], "succeeded")
        self.assertEqual(
            question_result["aggregateAnswerReview"]["classification"],
            "non_target",
        )
        self.assertEqual(
            question_result["aggregateAnswerReview"]["decision"],
            "approve",
        )
        self.assertTrue(
            question_result["aggregateAnswerReview"]["adjudicated"]
        )

    def test_adjudication_hold_is_the_only_terminal_disagreement_hold(self):
        question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            aggregate_review_overrides={
                (question_id, 1): {
                    "classification": "target",
                    "decision": "approve",
                },
                (question_id, 2): {
                    "classification": "non_target",
                    "decision": "approve",
                },
                (question_id, 3): {
                    "classification": "hold",
                    "decision": "hold",
                    "issueCodes": ["ambiguous_target"],
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(
                root,
                1,
                question_body_text="A　最初の記述。\nB　次の記述。",
            )

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(app_server.aggregate_review_calls), 3)
        attempts = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "blocked")
        self.assertEqual(
            attempts[0]["feedback"]["issues"][0]["code"],
            "aggregate_review_hold",
        )

    def test_parallel_batches_preserve_all_review_checkpoints_and_never_exceed_two_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=PerQuestionQueueAppServer(),
            )
            question_ids = [
                value["questionId"] for value in parent["questionExecutions"]
            ]
            barrier = threading.Barrier(len(question_ids))
            errors = []

            def write_batch(question_id):
                signature = {
                    "sourceHash": "sha256:" + "1" * 64,
                    "stableParentIdentity": {
                        "field": "sourceQuestionKey",
                        "value": f"source:{question_id}",
                    },
                    "model": "gpt-5.5",
                    "reasoningEffort": "high",
                    "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
                }
                try:
                    barrier.wait()
                    for slot in (1, 2):
                        reserved = coordinator.store.reserve_aggregate_review_slots(
                            "new-exam",
                            parent["runId"],
                            [(question_id, signature, slot)],
                        )[question_id]
                        self.assertEqual(reserved["status"], "reserved")
                        coordinator.store.resolve_aggregate_review_slots(
                            "new-exam",
                            parent["runId"],
                            [
                                (
                                    question_id,
                                    signature,
                                    slot,
                                    {"slot": slot},
                                    {
                                        "reviewNumber": slot,
                                        "threadId": f"thread-{question_id}-{slot}",
                                        "sessionId": f"session-{question_id}-{slot}",
                                        "turnId": f"turn-{question_id}-{slot}",
                                        "model": "gpt-5.5",
                                        "reasoningEffort": "high",
                                    },
                                )
                            ],
                        )
                    coordinator.store.store_aggregate_review_consensus(
                        "new-exam",
                        parent["runId"],
                        question_id,
                        signature,
                        {"decision": "approve"},
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [
                threading.Thread(target=write_batch, args=(question_id,))
                for question_id in question_ids
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            completed = coordinator.store.get("new-exam", parent["runId"])
            repeated_statuses = []
            for question_id in question_ids:
                for slot in (1, 2):
                    repeated = coordinator.store.reserve_aggregate_review_slot(
                        "new-exam",
                        parent["runId"],
                        question_id,
                        {
                            "sourceHash": "sha256:" + "1" * 64,
                            "stableParentIdentity": {
                                "field": "sourceQuestionKey",
                                "value": f"source:{question_id}",
                            },
                            "model": "gpt-5.5",
                            "reasoningEffort": "high",
                            "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
                        },
                        slot,
                    )
                    repeated_statuses.append(repeated["status"])

        self.assertEqual(errors, [])
        checkpoints = completed["aggregateReviewCheckpoints"]
        self.assertEqual(set(checkpoints), set(question_ids))
        for question_id in question_ids:
            checkpoint = checkpoints[question_id]
            self.assertEqual(set(checkpoint["slots"]), {"1", "2"})
            self.assertNotIn("reviews", checkpoint)
            self.assertNotIn("executions", checkpoint)
        self.assertEqual(repeated_statuses, ["resolved"] * 4)

    def test_checkpoint_batches_skip_parent_load_and_write_one_shard_per_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=PerQuestionQueueAppServer(),
            )
            question_ids = [
                value["questionId"] for value in parent["questionExecutions"]
            ]

            def signature(question_id):
                return {
                    "sourceHash": "sha256:" + "7" * 64,
                    "stableParentIdentity": {
                        "field": "sourceQuestionKey",
                        "value": f"source:{question_id}",
                    },
                    "model": "gpt-5.5",
                    "reasoningEffort": "high",
                    "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
                }

            signatures = {value: signature(value) for value in question_ids}
            with patch.object(
                coordinator.store,
                "_load_manifest",
                wraps=coordinator.store._load_manifest,
            ) as loads, patch.object(
                coordinator.store,
                "_write_aggregate_checkpoint_sidecar",
                wraps=coordinator.store._write_aggregate_checkpoint_sidecar,
            ) as writes:
                coordinator.store.reserve_aggregate_review_slots(
                    "new-exam",
                    parent["runId"],
                    [(value, signatures[value], 1) for value in question_ids],
                )
            self.assertEqual(loads.call_count, 0)
            self.assertEqual(writes.call_count, len(question_ids))

            def execution(question_id):
                return {
                    "reviewNumber": 1,
                    "threadId": f"thread-{question_id}",
                    "sessionId": f"session-{question_id}",
                    "turnId": f"turn-{question_id}",
                    "model": "gpt-5.5",
                    "reasoningEffort": "high",
                }

            before = coordinator.store.get("new-exam", parent["runId"])[
                "aggregateReviewCheckpoints"
            ]
            invalid_execution = execution(question_ids[1])
            invalid_execution["threadId"] = ""
            with self.assertRaisesRegex(Exception, "execution evidence"):
                coordinator.store.resolve_aggregate_review_slots(
                    "new-exam",
                    parent["runId"],
                    [
                        (
                            question_ids[0],
                            signatures[question_ids[0]],
                            1,
                            {"review": 1},
                            execution(question_ids[0]),
                        ),
                        (
                            question_ids[1],
                            signatures[question_ids[1]],
                            1,
                            {"review": 1},
                            invalid_execution,
                        ),
                    ],
                )
            self.assertEqual(
                coordinator.store.get("new-exam", parent["runId"])[
                    "aggregateReviewCheckpoints"
                ],
                before,
            )

            with patch.object(
                coordinator.store,
                "_load_manifest",
                wraps=coordinator.store._load_manifest,
            ) as loads, patch.object(
                coordinator.store,
                "_write_aggregate_checkpoint_sidecar",
                wraps=coordinator.store._write_aggregate_checkpoint_sidecar,
            ) as writes:
                coordinator.store.resolve_aggregate_review_slots(
                    "new-exam",
                    parent["runId"],
                    [
                        (
                            value,
                            signatures[value],
                            1,
                            {"review": 1},
                            execution(value),
                        )
                        for value in question_ids
                    ],
                )
            self.assertEqual(loads.call_count, 0)
            self.assertEqual(writes.call_count, len(question_ids))

            coordinator.store.reserve_aggregate_review_slots(
                "new-exam",
                parent["runId"],
                [(value, signatures[value], 2) for value in question_ids],
            )
            coordinator.store.resolve_aggregate_review_slots(
                "new-exam",
                parent["runId"],
                [
                    (
                        value,
                        signatures[value],
                        2,
                        {"review": 2},
                        {
                            **execution(value),
                            "reviewNumber": 2,
                            "threadId": f"thread-2-{value}",
                        },
                    )
                    for value in question_ids
                ],
            )
            with patch.object(
                coordinator.store,
                "_load_manifest",
                wraps=coordinator.store._load_manifest,
            ) as loads, patch.object(
                coordinator.store,
                "_write_aggregate_checkpoint_sidecar",
                wraps=coordinator.store._write_aggregate_checkpoint_sidecar,
            ) as writes:
                coordinator.store.store_aggregate_review_consensuses(
                    "new-exam",
                    parent["runId"],
                    [
                        (value, signatures[value], {"decision": "approve"})
                        for value in question_ids
                    ],
                )
            self.assertEqual(loads.call_count, 0)
            self.assertEqual(writes.call_count, len(question_ids))

    def test_prethread_reservation_cancellation_is_atomic_and_preserves_other_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=PerQuestionQueueAppServer(),
            )
            first_id, second_id = [
                value["questionId"] for value in parent["questionExecutions"]
            ]

            def signature(question_id):
                return {
                    "sourceHash": "sha256:" + "4" * 64,
                    "stableParentIdentity": {
                        "field": "sourceQuestionKey",
                        "value": question_id.replace(
                            "new-exam-2026-q", "new-exam:2026:q"
                        ),
                    },
                    "model": "gpt-5.5",
                    "reasoningEffort": "high",
                    "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
                }

            first_signature = signature(first_id)
            second_signature = signature(second_id)
            coordinator.store.reserve_aggregate_review_slot(
                "new-exam", parent["runId"], first_id, first_signature, 1
            )
            coordinator.store.resolve_aggregate_review_slot(
                "new-exam",
                parent["runId"],
                first_id,
                first_signature,
                1,
                review={"slot": 1},
                execution={
                    "reviewNumber": 1,
                    "threadId": "thread-existing",
                    "sessionId": "session-existing",
                    "turnId": "turn-existing",
                    "model": "gpt-5.5",
                    "reasoningEffort": "high",
                },
            )
            reservations = coordinator.store.reserve_aggregate_review_slots(
                "new-exam",
                parent["runId"],
                [
                    (first_id, first_signature, 2),
                    (second_id, second_signature, 1),
                ],
            )
            before = coordinator.store.get("new-exam", parent["runId"])[
                "aggregateReviewCheckpoints"
            ]
            invalid_second = copy.deepcopy(reservations[second_id]["slot"])
            invalid_second["reservedAt"] = "different"

            with self.assertRaisesRegex(Exception, "取消対象"):
                coordinator.store.cancel_unstarted_aggregate_review_slots(
                    "new-exam",
                    parent["runId"],
                    [
                        (
                            first_id,
                            first_signature,
                            2,
                            reservations[first_id]["slot"],
                        ),
                        (second_id, second_signature, 1, invalid_second),
                    ],
                )
            unchanged = coordinator.store.get("new-exam", parent["runId"])[
                "aggregateReviewCheckpoints"
            ]
            self.assertEqual(unchanged, before)

            original_write = (
                coordinator.store._write_aggregate_checkpoint_sidecar
            )

            def ignore_cancellation_write(*_args):
                return None

            coordinator.store._write_aggregate_checkpoint_sidecar = (
                ignore_cancellation_write
            )
            try:
                with self.assertRaisesRegex(Exception, "再読検証"):
                    coordinator.store.cancel_unstarted_aggregate_review_slots(
                        "new-exam",
                        parent["runId"],
                        [
                            (
                                first_id,
                                first_signature,
                                2,
                                reservations[first_id]["slot"],
                            ),
                            (
                                second_id,
                                second_signature,
                                1,
                                reservations[second_id]["slot"],
                            ),
                        ],
                    )
            finally:
                coordinator.store._write_aggregate_checkpoint_sidecar = (
                    original_write
                )
            after_noop = coordinator.store.get("new-exam", parent["runId"])[
                "aggregateReviewCheckpoints"
            ]
            self.assertEqual(after_noop, before)

            coordinator.store.cancel_unstarted_aggregate_review_slots(
                "new-exam",
                parent["runId"],
                [
                    (
                        first_id,
                        first_signature,
                        2,
                        reservations[first_id]["slot"],
                    ),
                    (
                        second_id,
                        second_signature,
                        1,
                        reservations[second_id]["slot"],
                    ),
                ],
            )
            after = coordinator.store.get("new-exam", parent["runId"])[
                "aggregateReviewCheckpoints"
            ]

        self.assertEqual(set(after), {first_id})
        self.assertEqual(set(after[first_id]["slots"]), {"1"})
        self.assertEqual(after[first_id]["slots"]["1"]["status"], "resolved")

    def test_cancellation_readback_failure_does_not_retry_external_model(self):
        class NoOpCancellationWriteAppServer(FlowAppServer):
            coordinator = None

            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                original_write = (
                    self.coordinator.store._write_aggregate_checkpoint_sidecar
                )

                def ignore_once(*_args):
                    self.coordinator.store._write_aggregate_checkpoint_sidecar = (
                        original_write
                    )

                self.coordinator.store._write_aggregate_checkpoint_sidecar = (
                    ignore_once
                )
                raise SubscriptionGateError("利用上限に達しています。")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = NoOpCancellationWriteAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            app_server.coordinator = coordinator

            result = coordinator._run_maintenance_flow(
                "new-exam", parent["runId"], lambda _message: None
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(app_server.calls), 1)
        self.assertIsNone(completed["pauseKind"])
        attempts = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "blocked")
        self.assertEqual(
            attempts[0]["feedback"]["issues"][0]["code"],
            "aggregate_review_checkpoint_integrity",
        )
        checkpoint = completed["aggregateReviewCheckpoints"][
            "new-exam-2026-q1"
        ]
        self.assertEqual(checkpoint["slots"]["1"]["status"], "started")

    def test_checkpoint_integrity_failures_block_once_without_more_model_turns(self):
        cases = (
            (
                "reserve",
                "reserve_aggregate_review_slots",
                "aggregate review slot予約を再読検証できません。",
                0,
            ),
            (
                "resolve",
                "resolve_aggregate_review_slots",
                "aggregate review slot確定を再読検証できません。",
                1,
            ),
            (
                "consensus",
                "store_aggregate_review_consensuses",
                "aggregate review consensusを再読検証できません。",
                2,
            ),
        )
        for label, method_name, message, expected_review_calls in cases:
            with self.subTest(failure=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                app_server = PerQuestionQueueAppServer()
                coordinator, _sync, _server, parent = self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type"],
                    app_server=app_server,
                )
                self._write_counted_sources(
                    root, 1, question_body_text="Aであり、Bである。"
                )
                checkpoint_at_failure = []

                def fail_integrity(*_args, **_kwargs):
                    checkpoint_at_failure.append(
                        copy.deepcopy(
                            coordinator.store.get("new-exam", parent["runId"]).get(
                                "aggregateReviewCheckpoints"
                            )
                            or {}
                        )
                    )
                    raise QualificationRunError(message)

                setattr(coordinator.store, method_name, fail_integrity)
                result = coordinator._run_maintenance_flow(
                    "new-exam", parent["runId"], lambda _message: None
                )
                completed = coordinator.store.get("new-exam", parent["runId"])

                self.assertEqual(result["queueStatus"], "partial")
                self.assertEqual(
                    len(app_server.aggregate_review_calls), expected_review_calls
                )
                self.assertEqual(app_server.calls, [])
                attempts = completed["questionExecutions"][0]["stages"][0][
                    "validationAttempts"
                ]
                self.assertEqual(len(attempts), 1)
                self.assertEqual(attempts[0]["status"], "blocked")
                self.assertEqual(
                    attempts[0]["feedback"]["issues"][0]["code"],
                    "aggregate_review_checkpoint_integrity",
                )
                self.assertEqual(len(checkpoint_at_failure), 1)
                self.assertEqual(
                    completed.get("aggregateReviewCheckpoints") or {},
                    checkpoint_at_failure[0],
                )

    def test_corrupt_review_checkpoint_and_execution_evidence_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=PerQuestionQueueAppServer(),
            )
            first_id, second_id = [
                value["questionId"] for value in parent["questionExecutions"]
            ]

            def signature(question_id):
                return {
                    "sourceHash": "sha256:" + "2" * 64,
                    "stableParentIdentity": {
                        "field": "sourceQuestionKey",
                        "value": f"source:{question_id}",
                    },
                    "model": "gpt-5.5",
                    "reasoningEffort": "high",
                    "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
                }

            corrupt = {
                **signature(first_id),
                "slots": {
                    "3": {"slot": 3, "status": "started"},
                },
                "consensus": None,
            }
            self._write_aggregate_checkpoint(
                coordinator.store,
                "new-exam",
                parent["runId"],
                first_id,
                corrupt,
            )
            mismatch = coordinator.store.reserve_aggregate_review_slot(
                "new-exam",
                parent["runId"],
                first_id,
                signature(first_id),
                1,
            )
            persisted_unknown = coordinator.store.get(
                "new-exam",
                parent["runId"],
            )["aggregateReviewCheckpoints"][first_id]

            missing_slots = {
                **signature(first_id),
                "consensus": None,
            }
            self._write_aggregate_checkpoint(
                coordinator.store,
                "new-exam",
                parent["runId"],
                first_id,
                missing_slots,
            )
            missing_slots_result = coordinator.store.reserve_aggregate_review_slot(
                "new-exam",
                parent["runId"],
                first_id,
                signature(first_id),
                1,
            )
            persisted_missing_slots = coordinator.store.get(
                "new-exam",
                parent["runId"],
            )["aggregateReviewCheckpoints"][first_id]

            coordinator.store.reserve_aggregate_review_slot(
                "new-exam",
                parent["runId"],
                second_id,
                signature(second_id),
                1,
            )
            with self.assertRaisesRegex(
                Exception,
                "execution evidence",
            ):
                coordinator.store.resolve_aggregate_review_slot(
                    "new-exam",
                    parent["runId"],
                    second_id,
                    signature(second_id),
                    1,
                    review={"slot": 1},
                    execution={
                        "reviewNumber": 1,
                        "threadId": "thread-1",
                        "sessionId": "session-1",
                        "turnId": "turn-1",
                        "model": "wrong-model",
                        "reasoningEffort": "high",
                    },
                )
            unresolved = coordinator.store.aggregate_review_checkpoint(
                "new-exam",
                parent["runId"],
                second_id,
            )
            with self.assertRaisesRegex(Exception, "重複予約"):
                coordinator.store.reserve_aggregate_review_slots(
                    "new-exam",
                    parent["runId"],
                    [
                        (second_id, signature(second_id), 1),
                        (second_id, signature(second_id), 2),
                    ],
                )

        self.assertEqual(mismatch["status"], "mismatch")
        self.assertIn("3", persisted_unknown["slots"])
        self.assertEqual(missing_slots_result["status"], "mismatch")
        self.assertEqual(persisted_missing_slots, missing_slots)
        self.assertEqual(unresolved["slots"]["1"]["status"], "started")

    def test_invalid_resolved_execution_evidence_is_preserved_and_not_rereviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=PerQuestionQueueAppServer(),
            )
            question_id = parent["questionExecutions"][0]["questionId"]
            signature = {
                "sourceHash": "sha256:" + "3" * 64,
                "stableParentIdentity": {
                    "field": "sourceQuestionKey",
                    "value": "new-exam:2026:q1",
                },
                "model": "gpt-5.5",
                "reasoningEffort": "high",
                "promptContractVersion": AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION,
            }
            valid_execution = {
                "reviewNumber": 1,
                "threadId": "thread-1",
                "sessionId": "session-1",
                "turnId": "turn-1",
                "model": "gpt-5.5",
                "reasoningEffort": "high",
            }
            coordinator.store.reserve_aggregate_review_slot(
                "new-exam", parent["runId"], question_id, signature, 1
            )
            coordinator.store.resolve_aggregate_review_slot(
                "new-exam",
                parent["runId"],
                question_id,
                signature,
                1,
                review={"slot": 1},
                execution=valid_execution,
            )
            original = coordinator.store.get("new-exam", parent["runId"])[
                "aggregateReviewCheckpoints"
            ][question_id]

            for field, invalid in (
                ("reviewNumber", 2),
                ("model", "wrong-model"),
                ("reasoningEffort", "low"),
                ("threadId", ""),
                ("sessionId", ""),
                ("turnId", ""),
            ):
                with self.subTest(field=field):
                    corrupt = copy.deepcopy(original)
                    corrupt["slots"]["1"]["execution"][field] = invalid
                    self._write_aggregate_checkpoint(
                        coordinator.store,
                        "new-exam",
                        parent["runId"],
                        question_id,
                        corrupt,
                    )
                    result = coordinator.store.reserve_aggregate_review_slot(
                        "new-exam", parent["runId"], question_id, signature, 1
                    )
                    persisted = coordinator.store.get(
                        "new-exam", parent["runId"]
                    )["aggregateReviewCheckpoints"][question_id]
                    self.assertEqual(result["status"], "mismatch")
                    self.assertEqual(persisted, corrupt)

    def test_invalid_persisted_execution_evidence_blocks_before_any_model_turn_and_is_preserved(self):
        source_text = "Aであり、Bである。"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 1, question_body_text=source_text)
            question_id = parent["questionExecutions"][0]["questionId"]
            invalid = self._invalid_resolved_aggregate_checkpoint(
                question_id, source_text
            )
            self._write_aggregate_checkpoint(
                coordinator.store,
                "new-exam",
                parent["runId"],
                question_id,
                copy.deepcopy(invalid),
            )

            result = coordinator._run_maintenance_flow(
                "new-exam", parent["runId"], lambda _message: None
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(app_server.aggregate_review_calls, [])
        self.assertEqual(app_server.calls, [])
        self.assertEqual(
            completed["aggregateReviewCheckpoints"][question_id], invalid
        )
        attempts = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual(len(attempts), 1, attempts)
        self.assertEqual(attempts[0]["status"], "blocked")

    def test_invalid_checkpoint_is_excluded_while_valid_question_continues(self):
        source_text = "Aであり、Bである。"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2, question_body_text=source_text)
            invalid_id, valid_id = [
                value["questionId"] for value in parent["questionExecutions"]
            ]
            invalid = self._invalid_resolved_aggregate_checkpoint(
                invalid_id, source_text
            )
            self._write_aggregate_checkpoint(
                coordinator.store,
                "new-exam",
                parent["runId"],
                invalid_id,
                copy.deepcopy(invalid),
            )

            result = coordinator._run_maintenance_flow(
                "new-exam", parent["runId"], lambda _message: None
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(app_server.aggregate_review_calls), 2)
        self.assertEqual(app_server.batch_calls, [(valid_id,)])
        self.assertEqual(
            completed["aggregateReviewCheckpoints"][invalid_id], invalid
        )
        stages = {
            value["questionId"]: value["stages"][0]
            for value in completed["questionExecutions"]
        }
        self.assertEqual(stages[invalid_id]["status"], "blocked")
        self.assertEqual(stages[valid_id]["status"], "validated")

    def test_missing_stable_parent_identity_blocks_before_any_model_turn(self):
        class MissingStableIdentityInventory(SourceOnlyInventory):
            def projected_input(
                self,
                qualification,
                list_group_id,
                source_record_ref,
            ):
                projected = super().projected_input(
                    qualification,
                    list_group_id,
                    source_record_ref,
                )
                record = copy.deepcopy(projected.record)
                record.pop("originalQuestionId", None)
                return SimpleNamespace(
                    record=record,
                    applied_files=projected.applied_files,
                    errors=projected.errors,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                MissingStableIdentityInventory(),
                ["question_type"],
                app_server=app_server,
            )
            source = (
                root
                / "output/new-exam/questions_json/2026/00_source/question_2026_1.json"
            )
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "reviewQuestionId": "new-exam-2026-q1",
                                "sourceRecordRef": "question_2026_1.json#0",
                                "questionBodyText": "Aであり、Bである。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = coordinator._run_maintenance_flow(
                "new-exam", parent["runId"], lambda _message: None
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(app_server.aggregate_review_calls, [])
        self.assertEqual(app_server.calls, [])
        attempts = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "blocked")
        self.assertEqual(
            attempts[0]["feedback"]["issues"][0]["code"],
            "stable_parent_identity",
        )

    def test_resumed_fresh_and_failed_questions_use_separate_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2)
            failed_question_id = parent["questionExecutions"][0]["questionId"]
            fresh_question_id = parent["questionExecutions"][1]["questionId"]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                failed_question_id,
                "question_type",
                validationAttempts=[
                    {
                        "attempt": 1,
                        "status": "failed",
                        "feedback": {"reason": "機械検査に失敗"},
                    }
                ],
            )

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        models_by_batch = {
            batch: kwargs["model"]
            for batch, (_question_id, _prompt, kwargs) in zip(
                app_server.batch_calls,
                app_server.calls,
                strict=True,
            )
        }
        self.assertEqual(
            models_by_batch[(fresh_question_id,)],
            "gpt-5.6-luna",
        )
        self.assertEqual(
            models_by_batch[(failed_question_id,)],
            "gpt-5.6-sol",
        )

    def test_blocked_candidate_stops_only_that_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer(
                failed_question_id="new-exam-2026-q2"
            )
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(completed["validatedQuestionCount"], 1)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["validated", "blocked"],
        )

    def test_ambiguous_target_is_blocked_before_model_without_stopping_sibling(self):
        def assert_resolvable(_root, _path, *, binding, aliases, cache=None):
            del aliases, cache
            if "question_2026_2.json" in binding.source_record_ref:
                raise ValueError("対象レコードが複数あります")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2)

            with patch(
                "tools.question_review_console.qualification_runs."
                "assert_target_resolvable",
                side_effect=assert_resolvable,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(app_server.batch_calls, [("new-exam-2026-q1",)])
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["validated", "blocked"],
        )

    def test_small_ten_question_input_uses_independent_concurrent_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            app_server.writer_delay = 0.1
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(10),
                ["question_type"],
                app_server=app_server,
                question_concurrency=10,
            )
            self._write_counted_sources(root, 10)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertGreater(app_server.max_active_writers, 1)
        self.assertLessEqual(app_server.max_active_writers, 10)
        self.assertEqual(len(app_server.batch_calls), 10)
        self.assertTrue(all(len(batch) == 1 for batch in app_server.batch_calls))

    def test_candidate_queue_wait_includes_aggregate_review_time(self):
        class SlowAggregateReviewAppServer(PerQuestionQueueAppServer):
            def run_turn(self, prompt, **kwargs):
                if "_aggregate_review_" in kwargs["work_type"]:
                    time.sleep(0.04)
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(1),
                ["question_type"],
                app_server=SlowAggregateReviewAppServer(),
                question_concurrency=1,
            )
            self._write_counted_sources(root, 1)
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            attempt = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )[0]

        self.assertEqual(result["queueStatus"], "succeeded")
        telemetry = attempt["modelTurnTelemetry"]
        self.assertGreaterEqual(telemetry["queueWaitSeconds"], 0.075)
        self.assertLess(
            telemetry["executorQueueWaitSeconds"],
            telemetry["queueWaitSeconds"],
        )
        self.assertEqual(
            completed["modelTurns"]["queueWaitSeconds"]["count"],
            3,
        )
        self.assertEqual(
            completed["modelTurns"]["queueWaitSeconds"]["total"],
            0.0,
        )

    def test_pipeline_uses_model_and_generic_tool_executors(self):
        class ThreadCapturingAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.candidate_thread_names = []

            def run_turn(self, prompt, **kwargs):
                if (
                    kwargs["work_type"]
                    == "maintenance_question_type_candidate"
                ):
                    self.candidate_thread_names.append(
                        threading.current_thread().name
                    )
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ThreadCapturingAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(1),
                ["question_type"],
                app_server=app_server,
                question_concurrency=1,
            )
            self._write_counted_sources(root, 1)
            input_thread_names = []
            patch_thread_names = []
            original_question_stage_spec = coordinator._question_stage_spec
            original_record_work_versions = coordinator._record_work_versions

            def capture_input_thread(*args, **kwargs):
                input_thread_names.append(threading.current_thread().name)
                return original_question_stage_spec(*args, **kwargs)

            def capture_patch_thread(plan):
                patch_thread_names.append(threading.current_thread().name)
                return original_record_work_versions(plan)

            coordinator._question_stage_spec = capture_input_thread
            coordinator._record_work_versions = capture_patch_thread
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertTrue(input_thread_names)
        self.assertTrue(patch_thread_names)
        self.assertTrue(app_server.candidate_thread_names)
        self.assertTrue(
            all(
                name.startswith("question-tool")
                for name in [*input_thread_names, *patch_thread_names]
            )
        )
        self.assertTrue(
            all(
                name.startswith("question-model")
                for name in app_server.candidate_thread_names
            )
        )
        self.assertFalse(
            any(
                name.startswith(("question-preparation", "question-commit"))
                for name in [
                    *input_thread_names,
                    *patch_thread_names,
                    *app_server.candidate_thread_names,
                ]
            )
        )

    def test_patch_tool_is_active_while_manifest_read_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(1),
                ["question_type"],
                app_server=PerQuestionQueueAppServer(),
                question_concurrency=1,
            )
            self._write_counted_sources(root, 1)
            original_get = coordinator.store.get
            read_entered = threading.Event()
            release_read = threading.Event()

            def slow_patch_get(qualification, run_id):
                if (
                    threading.current_thread().name.startswith(
                        "question-tool"
                    )
                    and str(run_id).startswith("qa-")
                    and not read_entered.is_set()
                ):
                    read_entered.set()
                    if not release_read.wait(10):
                        raise AssertionError("manifest read release timed out")
                return original_get(qualification, run_id)

            coordinator.store.get = slow_patch_get
            result_holder = {}
            patch_tool_started = threading.Event()
            active_snapshot = {}
            original_patch_tool_started = (
                _PipelineRuntimeTelemetry.patch_tool_started
            )

            def capture_patch_tool_started(telemetry, child_id, **kwargs):
                original_patch_tool_started(telemetry, child_id, **kwargs)
                active_snapshot.update(telemetry.patch_tool_snapshot())
                patch_tool_started.set()

            def run_flow():
                result_holder["result"] = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )

            with patch.object(
                _PipelineRuntimeTelemetry,
                "patch_tool_started",
                new=capture_patch_tool_started,
            ):
                thread = threading.Thread(target=run_flow)
                thread.start()
                try:
                    self.assertTrue(patch_tool_started.wait(5))
                    self.assertTrue(read_entered.wait(5))
                    self.assertEqual(active_snapshot.get("inFlight"), 1)
                    self.assertEqual(active_snapshot.get("peakInFlight"), 1)
                finally:
                    release_read.set()
                    thread.join(10)
                    coordinator.store.get = original_get

            self.assertFalse(thread.is_alive())
            self.assertEqual(
                result_holder["result"]["queueStatus"],
                "succeeded",
            )
            completed = original_get("new-exam", parent["runId"])
            self.assertEqual(completed["patchTools"]["inFlight"], 0)
            self.assertEqual(completed["patchTools"]["peakInFlight"], 1)

    def test_patch_tool_wait_does_not_consume_model_turn_capacity(self):
        question_count = 21

        class ModelStartTrackingAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.candidate_starts = 0
                self.candidate_start_lock = threading.Lock()
                self.all_candidates_started = threading.Event()

            def run_turn(self, prompt, **kwargs):
                if kwargs["work_type"] == "maintenance_question_type_candidate":
                    with self.candidate_start_lock:
                        self.candidate_starts += 1
                        if self.candidate_starts == question_count:
                            self.all_candidates_started.set()
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ModelStartTrackingAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(question_count),
                ["question_type"],
                app_server=app_server,
                question_concurrency=10,
            )
            self._write_counted_sources(root, question_count)
            original_record_work_versions = coordinator._record_work_versions
            patch_tool_entered = threading.Event()

            def hold_first_patch_tool(plan):
                patch_tool_entered.set()
                if not app_server.all_candidates_started.wait(10):
                    raise AssertionError(
                        "patch tool待ちがmodel turnを占有しました。"
                    )
                return original_record_work_versions(plan)

            coordinator._record_work_versions = hold_first_patch_tool
            try:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            finally:
                coordinator._record_work_versions = original_record_work_versions
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertTrue(patch_tool_entered.is_set())
        self.assertTrue(app_server.all_candidates_started.is_set())
        self.assertEqual(app_server.candidate_starts, question_count)
        self.assertEqual(completed["modelTurns"]["inFlight"], 0)
        self.assertGreaterEqual(completed["modelTurns"]["peakInFlight"], 1)
        self.assertLessEqual(completed["modelTurns"]["peakInFlight"], 10)
        self.assertEqual(
            completed["questionWindow"]["refillLatencySeconds"]["count"],
            11,
        )
        self.assertNotIn("refillLatencySeconds", completed["modelTurns"])
        self.assertEqual(completed["patchTools"]["inFlight"], 0)
        self.assertEqual(
            completed["patchTools"]["startedCount"],
            question_count,
        )
        self.assertEqual(
            completed["patchTools"]["finishedCount"],
            question_count,
        )

    def test_one_hundred_questions_start_one_hundred_independent_model_turns(self):
        class OneHundredTurnAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.started = 0
                self.started_lock = threading.Lock()
                self.all_started = threading.Event()

            def run_turn(self, prompt, **kwargs):
                if kwargs["work_type"] == "maintenance_question_type_candidate":
                    observe = kwargs.get("on_model_turn_event")

                    def observe_and_hold(event):
                        result = observe(event) if callable(observe) else None
                        if str(event.get("event") or "") != "started":
                            return result
                        with self.started_lock:
                            self.started += 1
                            if self.started == 100:
                                self.all_started.set()
                        if not self.all_started.wait(10):
                            raise AssertionError(
                                "100問のmodel turnが同時に開始しませんでした。"
                            )
                        return result

                    kwargs["on_model_turn_event"] = observe_and_hold
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = OneHundredTurnAppServer()
            app_server.writer_delay = 0.1
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(100),
                ["question_type"],
                app_server=app_server,
                question_concurrency=100,
            )
            self._write_counted_sources(root, 100)
            parent_path = coordinator.store._manifest_path(
                "new-exam",
                parent["runId"],
            )
            parent_write_count = 0
            parent_write_lock = threading.Lock()
            original_write = coordinator.store._write_manifest

            def count_parent_writes(path, manifest):
                nonlocal parent_write_count
                if path == parent_path:
                    with parent_write_lock:
                        parent_write_count += 1
                original_write(path, manifest)

            coordinator.store._write_manifest = count_parent_writes
            try:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            finally:
                coordinator.store._write_manifest = original_write
            completed = coordinator.store.get("new-exam", parent["runId"])
            attempts = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertTrue(app_server.all_started.is_set())
        self.assertEqual(app_server.started, 100)
        self.assertEqual(len(app_server.batch_calls), 100)
        self.assertTrue(all(len(batch) == 1 for batch in app_server.batch_calls))
        self.assertEqual(
            completed["parallelStrategy"],
            "rolling_question_window",
        )
        self.assertTrue(
            all(
                attempt.get("parallelStrategy") == "question_turn"
                for attempt in attempts
            )
        )
        self.assertEqual(completed["modelBatchSize"], 1)
        self.assertEqual(completed["modelWorkerLimit"], 100)
        self.assertEqual(completed["modelPeakPendingFutureCount"], 100)
        self.assertEqual(
            completed["modelTurns"],
            {
                "measurement": "app_server_protocol_notifications",
                "capacity": 100,
                "inFlight": 0,
                "peakInFlight": 100,
                "startedCount": 300,
                "finishedCount": 300,
                "queueWaitSeconds": completed["modelTurns"][
                    "queueWaitSeconds"
                ],
                "durationSeconds": completed["modelTurns"][
                    "durationSeconds"
                ],
            },
        )
        self.assertEqual(
            completed["questionWindow"]["refillLatencySeconds"]["count"],
            0,
        )
        self.assertEqual(
            completed["modelTurns"]["queueWaitSeconds"]["count"],
            300,
        )
        self.assertEqual(
            completed["modelTurns"]["durationSeconds"]["count"],
            300,
        )
        self.assertEqual(completed["patchTools"]["inFlight"], 0)
        self.assertEqual(completed["patchTools"]["startedCount"], 100)
        self.assertEqual(completed["patchTools"]["finishedCount"], 100)
        self.assertEqual(
            completed["patchTools"]["queueWaitSeconds"]["count"],
            100,
        )
        self.assertEqual(
            completed["patchTools"]["lockWaitSeconds"]["count"],
            100,
        )
        self.assertEqual(
            len(_question_attempt_ids(completed)),
            100,
        )
        self.assertTrue(
            all(
                isinstance(attempt.get("preparedCandidate"), Mapping)
                and attempt.get("patchApplyStartedAt")
                and attempt.get("modelTurnTelemetry", {}).get(
                    "modelTurnStartedAt"
                )
                and attempt.get("modelTurnTelemetry", {}).get(
                    "modelTurnFinishedAt"
                )
                and attempt.get("modelTurnTelemetry", {}).get(
                    "executorQueueWaitSeconds"
                )
                is not None
                and attempt.get("patchToolQueueWaitSeconds") is not None
                and attempt.get("patchToolLockWaitSeconds") is not None
                and attempt.get("patchToolLockPaths")
                for attempt in attempts
            )
        )
        self.assertEqual(completed["childRunIds"], [])
        # Candidate durability belongs to the 100 independent question state
        # files. The parent may still receive fixed lifecycle and 15-second
        # progress writes, but it must not receive one candidate write per
        # question.
        self.assertLess(parent_write_count, 100)
        self.assertEqual(completed["validatedQuestionCount"], 100)

    def test_one_turn_timeout_is_retried_without_reducing_capacity(self):
        class TimeoutOnceAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.timed_out = False

            def run_turn(self, prompt, **kwargs):
                if (
                    not self.timed_out
                    and kwargs["work_type"]
                    == "maintenance_question_type_candidate"
                ):
                    self.timed_out = True
                    question_ids = self._question_ids(prompt)
                    with self._lock:
                        self.batch_calls.append(tuple(question_ids))
                    kwargs["on_thread_started"]("thread-timeout", "session-timeout")
                    kwargs["on_turn_started"]("thread-timeout", "turn-timeout")
                    raise CodexTurnTimeoutError("turnが時間切れになりました。")
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = TimeoutOnceAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(6),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 6)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(len(app_server.batch_calls), 7)
        self.assertTrue(all(len(batch) == 1 for batch in app_server.batch_calls))
        counts = {
            question_id: app_server.batch_calls.count((question_id,))
            for question_id in (
                f"new-exam-2026-q{number}" for number in range(1, 7)
            )
        }
        self.assertEqual(sorted(counts.values()), [1, 1, 1, 1, 1, 2])
        self.assertEqual(completed["adaptiveScheduler"]["parallelTurns"], 5)

    def test_timeout_keeps_started_review_slot_and_blocks_without_rerun(self):
        class ReviewTimeoutAppServer(PerQuestionQueueAppServer):
            review_starts = 0

            def run_turn(self, prompt, **kwargs):
                if (
                    self.review_starts == 0
                    and "_aggregate_review_" in kwargs["work_type"]
                ):
                    self.review_starts += 1
                    kwargs["on_thread_started"](
                        "thread-review-timeout",
                        "session-review-timeout",
                    )
                    kwargs["on_turn_started"](
                        "thread-review-timeout",
                        "turn-review-timeout",
                    )
                    raise CodexTurnTimeoutError("review turnが時間切れになりました。")
                if "_aggregate_review_" in kwargs["work_type"]:
                    self.review_starts += 1
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ReviewTimeoutAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 1)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(app_server.review_starts, 1)
        checkpoint = completed["aggregateReviewCheckpoints"][
            "new-exam-2026-q1"
        ]
        self.assertEqual(set(checkpoint["slots"]), {"1"})
        self.assertEqual(checkpoint["slots"]["1"]["status"], "started")
        attempts = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ]
        self.assertEqual([value["status"] for value in attempts], ["failed", "blocked"])

    def test_terminal_failed_review_slots_are_retried_without_losing_siblings(self):
        class TerminalFailureAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.review_attempts = {}
                self.failed_threads = {}

            def run_turn(self, prompt, **kwargs):
                work_type = kwargs["work_type"]
                if "_aggregate_review_" not in work_type:
                    return super().run_turn(prompt, **kwargs)
                question_id = str(self._candidate_questions(prompt)[0]["questionId"])
                review_number = int(
                    work_type.split("_aggregate_review_", 1)[1].split("_", 1)[0]
                )
                key = (question_id, review_number)
                attempt = self.review_attempts.get(key, 0) + 1
                self.review_attempts[key] = attempt
                should_fail = (
                    question_id == "new-exam-2026-q2" and review_number == 1
                ) or (
                    question_id != "new-exam-2026-q2" and review_number == 2
                )
                if should_fail and attempt == 1:
                    thread_id = f"failed-{question_id}-slot-{review_number}"
                    turn_id = f"{thread_id}-turn"
                    kwargs["on_thread_started"](thread_id, f"{thread_id}-session")
                    kwargs["on_turn_started"](thread_id, turn_id)
                    self.failed_threads[key] = thread_id
                    raise CodexTerminalTurnFailedError(
                        "terminal capacity failure",
                        thread_id=thread_id,
                        turn_id=turn_id,
                        status="failed",
                        error={"code": "model_at_capacity"},
                    )
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = TerminalFailureAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(5),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 5)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(completed["validatedQuestionCount"], 5)
        self.assertEqual(completed["blockedQuestionCount"], 0)
        for number in range(1, 6):
            question_id = f"new-exam-2026-q{number}"
            checkpoint = completed["aggregateReviewCheckpoints"][question_id]
            self.assertEqual(set(checkpoint["slots"]), {"1", "2"})
            self.assertTrue(
                all(
                    slot["status"] == "resolved"
                    for slot in checkpoint["slots"].values()
                )
            )
            failed_slot = 1 if number == 2 else 2
            resolved_execution = checkpoint["slots"][str(failed_slot)]["execution"]
            self.assertNotEqual(
                resolved_execution["threadId"],
                app_server.failed_threads[(question_id, failed_slot)],
            )
            self.assertEqual(
                app_server.review_attempts[(question_id, failed_slot)],
                2,
            )
            sibling_slot = 2 if failed_slot == 1 else 1
            self.assertEqual(
                app_server.review_attempts[(question_id, sibling_slot)],
                1,
            )

    def test_server_rebases_validated_candidate_into_canonical_patch(self):
        class ServerCandidateAppServer(PerQuestionQueueAppServer):
            def run_turn(self, prompt, **kwargs):
                work_type = kwargs["work_type"]
                if work_type == "maintenance_question_type_candidate":
                    question_id = str(self._candidate_questions(prompt)[0]["questionId"])
                    stage_id = "question_type"
                    patch_relative = (
                        "output/new-exam/questions_json/2026/10_questionType_fixed/"
                        "question_2026_1_questionType_fixed.json"
                    )
                    self.changed_files_by_work_item[(question_id, stage_id)] = [
                        patch_relative
                    ]
                result = super().run_turn(prompt, **kwargs)
                if work_type != "maintenance_question_type_candidate":
                    return result
                payload = json.loads(result.final_message)
                payload["update"]["setFields"] = [
                    {"field": "questionType", "value": "flash_card"},
                    {"field": "isCalculationQuestion", "value": False},
                ]
                return replace(
                    result,
                    final_message=json.dumps(payload, ensure_ascii=False),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ServerCandidateAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            source_path = (
                root
                / "output/new-exam/questions_json/2026/00_source/"
                "question_2026_1.json"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "original_question_id": "new-exam-2026-q1",
                                "sourceQuestionKey": "new-exam:2026:q1",
                                "reviewQuestionId": "new-exam-2026-q1",
                                "sourceRecordRef": "question_2026_1.json#0",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            child = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )[0]
            patch_path = root / child["result"]["changedFiles"][0]
            records = json.loads(patch_path.read_text(encoding="utf-8"))
            work_version_relative = str(
                coordinator.work_versions.question_path_for(
                    {
                        **parent["progressTargets"][0],
                        "qualification": "new-exam",
                    }
                ).relative_to(root.resolve())
            )
            work_version_payload = json.loads(
                (root / work_version_relative).read_text(encoding="utf-8")
            )
            legacy_work_version_exists = (
                root
                / "output/question_review_console/new-exam/2026/"
                "work_versions.json"
            ).exists()
            workspace_exists = (
                coordinator.store.run_directory(
                    "new-exam",
                    child["runId"],
                )
                / "candidate_workspaces"
            ).exists()

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertTrue(child["receiptValidated"])
        self.assertEqual(records[0]["questionType"], "flash_card")
        self.assertEqual(
            set(child["patchToolLockPaths"]),
            {
                child["result"]["changedFiles"][0],
                work_version_relative,
            },
        )
        self.assertEqual(
            work_version_payload["schemaVersion"],
            "question-work-versions/v4",
        )
        self.assertEqual(len(work_version_payload["questions"]), 1)
        self.assertFalse(legacy_work_version_exists)
        self.assertTrue(child["patchApplyStartedAt"])
        self.assertIsNotNone(child["patchToolQueueWaitSeconds"])
        self.assertIsNotNone(child["patchToolLockWaitSeconds"])
        self.assertFalse(workspace_exists)

    def test_checkpoint_failure_rolls_back_patch_and_blocks_publication(self):
        patch_relative = (
            "output/new-exam/questions_json/2026/10_questionType_fixed/"
            "question_2026_1_questionType_fixed.json"
        )
        question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            changed_files_by_work_item={
                (question_id, "question_type"): [patch_relative]
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            source_path = (
                root
                / "output/new-exam/questions_json/2026/00_source/"
                "question_2026_1.json"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "original_question_id": question_id,
                                "sourceQuestionKey": "new-exam:2026:q1",
                                "reviewQuestionId": question_id,
                                "sourceRecordRef": "question_2026_1.json#0",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_update = coordinator.store.update
            failed_checkpoints = 0

            def fail_first_success_checkpoint(qualification, run_id, **changes):
                nonlocal failed_checkpoints
                results = changes.get("batchQuestionResults") or []
                if (
                    failed_checkpoints == 0
                    and changes.get("executionPhase")
                    == "server_candidate_checkpoint"
                    and results
                    and results[-1].get("status") == "succeeded"
                ):
                    failed_checkpoints += 1
                    raise OSError("checkpoint unavailable")
                return original_update(qualification, run_id, **changes)

            with patch.object(
                coordinator.store,
                "update",
                side_effect=fail_first_success_checkpoint,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )
            patch_exists = (root / patch_relative).is_file()
            work_version_exists = any(
                (
                    root
                    / "output/question_review_console/new-exam/2026/"
                    "work_versions"
                ).glob("*.json")
            )

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(failed_checkpoints, 1)
        self.assertEqual(len(app_server.batch_calls), 1)
        self.assertEqual(
            [
                value["status"]
                for child in children
                for value in child["batchQuestionResults"]
            ],
            ["failed"],
        )
        self.assertEqual(children[0]["result"]["changedFiles"], [])
        self.assertEqual(
            children[0]["batchQuestionResults"][0]["changedFiles"],
            [],
        )
        self.assertEqual(
            children[0]["rollback"]["status"],
            "succeeded",
        )
        self.assertTrue(children[0]["canonicalWriteStarted"])
        self.assertTrue(children[0]["writeAttributionVerified"])
        self.assertFalse(patch_exists)
        self.assertFalse(work_version_exists)

    def test_successful_rollback_does_not_stop_the_next_question(self):
        patch_relative = (
            "output/new-exam/questions_json/2026/10_questionType_fixed/"
            "question_2026_1_questionType_fixed.json"
        )
        failed_question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            changed_files_by_work_item={
                (failed_question_id, "question_type"): [patch_relative]
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
                question_concurrency=1,
            )
            self._write_counted_sources(root, 2)
            original_update = coordinator.store.update
            failed_checkpoints = 0

            def fail_first_success_checkpoint(qualification, run_id, **changes):
                nonlocal failed_checkpoints
                results = changes.get("batchQuestionResults") or []
                if (
                    failed_checkpoints == 0
                    and changes.get("executionPhase")
                    == "server_candidate_checkpoint"
                    and results
                    and results[-1].get("status") == "succeeded"
                ):
                    failed_checkpoints += 1
                    raise OSError("checkpoint unavailable")
                return original_update(qualification, run_id, **changes)

            with patch.object(
                coordinator.store,
                "update",
                side_effect=fail_first_success_checkpoint,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )
            failed_patch_exists = (root / patch_relative).exists()

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(failed_checkpoints, 1)
        self.assertEqual(len(app_server.batch_calls), 2)
        self.assertEqual(completed["validatedQuestionCount"], 1)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        failed_child = next(
            child
            for child in children
            if child.get("rollback", {}).get("status") == "succeeded"
        )
        succeeded_child = next(
            child
            for child in children
            if any(
                value.get("status") == "succeeded"
                for value in child.get("batchQuestionResults") or []
            )
        )
        self.assertEqual(
            failed_child["progressTargets"][0]["id"],
            failed_question_id,
        )
        self.assertEqual(
            succeeded_child["progressTargets"][0]["id"],
            "new-exam-2026-q2",
        )
        self.assertFalse(failed_patch_exists)

    def test_shared_law_sidecar_rollback_preserves_the_sibling_commit(self):
        class TwoLawQuestionInventory(CountedSourceInventory):
            def __init__(self):
                super().__init__(2)

            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                for question in group["questions"]:
                    question["isLawRelated"] = True
                    question["projected"] = {
                        **question["projected"],
                        "examYear": 2026,
                        "isLawRelated": True,
                        "lawGroundedExplanationNotNeeded": False,
                        "choiceTextList": ["法令上の記述"],
                        "correctChoiceText": ["正しい"],
                        "explanationText": [
                            "正しい。ガス事業法第2条の定義に該当する。"
                        ],
                        "lawReferences": [
                            [
                                {
                                    "role": "current_basis",
                                    "scope": "choice",
                                    "choiceIndex": 0,
                                    "lawId": "329AC0000000051",
                                    "lawTitle": "ガス事業法",
                                    "referenceDate": "2026-07-24",
                                    "article": "第2条",
                                    "verificationStatus": "verified",
                                    "source": (
                                        "https://elaws.e-gov.go.jp/document"
                                        "?lawid=329AC0000000051"
                                    ),
                                }
                            ]
                        ],
                        "lawRevisionFacts": [
                            {
                                "auditStatus": "same_as_current",
                                "reviewState": "secondary_verified",
                                "reconciliationStatus": "matched",
                                "current": {"correctChoiceText": "正しい"},
                                "examTime": {"correctChoiceText": "正しい"},
                                "differenceFacts": [],
                                "answerImpactFacts": [],
                                "notes": [],
                                "evidenceSummary": {
                                    "verdict": "correct",
                                    "explanationText": "一次情報と照合した。",
                                    "differenceSummary": "差分なし。",
                                    "promptContext": "一次情報との照合。",
                                    "displayRefIds": [],
                                    "refs": [],
                                },
                            }
                        ],
                    }
                    question["projected"].pop("listGroupId", None)
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoLawQuestionInventory(),
                ["law_audit"],
                app_server=app_server,
                question_concurrency=5,
            )
            original_update = coordinator.store.update
            failed_checkpoints = 0

            def fail_first_success_checkpoint(qualification, run_id, **changes):
                nonlocal failed_checkpoints
                results = changes.get("batchQuestionResults") or []
                if (
                    failed_checkpoints == 0
                    and changes.get("executionPhase")
                    == "server_candidate_checkpoint"
                    and results
                    and results[-1].get("status") == "succeeded"
                ):
                    failed_checkpoints += 1
                    raise OSError("checkpoint unavailable")
                return original_update(qualification, run_id, **changes)

            with patch.object(
                coordinator.store,
                "update",
                side_effect=fail_first_success_checkpoint,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )
            sidecar = (
                root
                / "output/new-exam/review/law_revision_audit/"
                "2026_law_revision_audit.jsonl"
            )
            self.assertTrue(sidecar.is_file(), completed)
            sidecar_rows = [
                json.loads(line)
                for line in sidecar.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(failed_checkpoints, 1)
        self.assertEqual(completed["validatedQuestionCount"], 1)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        self.assertEqual(len(sidecar_rows), 1)
        self.assertEqual(sidecar_rows[0]["listGroupId"], "2026")
        self.assertEqual(
            {
                child["rollback"]["status"]
                for child in children
                if child.get("rollback")
            },
            {"succeeded", "not_required"},
        )
        self.assertTrue(
            all(child.get("deltaUnknown") is not True for child in children)
        )

    def test_validation_failure_never_writes_and_closes_transaction(self):
        patch_relative = (
            "output/new-exam/questions_json/2026/10_questionType_fixed/"
            "question_2026_1_questionType_fixed.json"
        )
        question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            changed_files_by_work_item={
                (question_id, "question_type"): [patch_relative]
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 1)
            original_validate = coordinator._validate_record_scope
            original_rollback = coordinator.store.rollback_baseline
            validation_calls = 0

            def fail_first_validation(*args, **kwargs):
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    raise OSError("record scope unavailable")
                return original_validate(*args, **kwargs)

            with patch.object(
                coordinator,
                "_validate_record_scope",
                side_effect=fail_first_validation,
            ), patch.object(
                coordinator.store,
                "rollback_baseline",
                wraps=original_rollback,
            ) as rollback:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )
            failed_child = next(
                child
                for child in children
                if any(
                    value.get("status") == "failed"
                    for value in child.get("batchQuestionResults") or []
                )
            )
            patch_exists = (root / patch_relative).exists()

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(validation_calls, 2)
        self.assertEqual(rollback.call_count, 0)
        self.assertEqual(len(app_server.batch_calls), 2)
        self.assertEqual(
            failed_child["batchQuestionResults"][0]["summary"],
            "record scope unavailable",
        )
        feedback = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ][0]["feedback"]
        self.assertEqual(feedback["status"], "retryable")
        self.assertIn(
            "machine_validation",
            [issue["code"] for issue in feedback["issues"]],
        )
        self.assertEqual(
            failed_child["rollback"]["status"],
            "not_required",
        )
        self.assertFalse(failed_child["canonicalWriteStarted"])
        self.assertTrue(patch_exists)

    def test_prewrite_contention_retries_one_question_without_stopping_siblings(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
                question_concurrency=5,
            )
            original_delta = coordinator.store.baseline_delta
            call_count = 0
            call_lock = threading.Lock()

            def one_transient_contention(qualification, run_id):
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    current = call_count
                if current == 1:
                    return ["output/new-exam/shared-patch.json"]
                return original_delta(qualification, run_id)

            with patch.object(
                coordinator.store,
                "baseline_delta",
                side_effect=one_transient_contention,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get(
                "new-exam",
                parent["runId"],
            )
            attempt_counts = sorted(
                len(question["stages"][0]["validationAttempts"])
                for question in completed["questionExecutions"]
            )
            feedback_codes = {
                issue["code"]
                for question in completed["questionExecutions"]
                for attempt in question["stages"][0]["validationAttempts"]
                for issue in (attempt.get("feedback") or {}).get("issues") or []
            }
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )
            reused_children = [
                child
                for child in children
                if child.get("preparedCandidateReusedReason")
                == "canonical_prewrite_contention"
            ]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(attempt_counts, [1, 2])
        self.assertIn("canonical_contention", feedback_codes)
        self.assertEqual(len(app_server.batch_calls), 2)
        self.assertEqual(len(reused_children), 1)
        self.assertTrue(
            reused_children[0]["preparedCandidateReusedFromAttemptId"]
        )

    def test_question_concurrency_defaults_to_one_hundred_and_allows_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, CountedSourceInventory(11)),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=FlowAppServer(),
            )
            preview_default = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
            )
            preview_ten = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=10,
            )
            preview_thirty_two = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=32,
            )
            preview_sixty_four = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=64,
            )
            preview_one_hundred = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=100,
            )
            started = coordinator.start(
                "new-exam",
                "question_type",
                "outdated",
                preview_default["previewToken"],
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=1,
            )
            parent = started["run"]

        self.assertEqual(preview_default["questionConcurrency"], 100)
        self.assertEqual(preview_thirty_two["questionConcurrency"], 32)
        self.assertEqual(preview_sixty_four["questionConcurrency"], 64)
        self.assertEqual(preview_one_hundred["questionConcurrency"], 100)
        self.assertEqual(preview_default["previewToken"], preview_ten["previewToken"])
        self.assertEqual(parent["questionConcurrency"], 1)
        self.assertEqual(parent["parallelWorkerLimit"], 1)
        self.assertEqual(parent["parallelStrategy"], "rolling_question_window")

    def test_fast_mode_is_rejected_before_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, CountedSourceInventory(2)),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=FlowAppServer(),
            )
            with self.assertRaisesRegex(ValueError, "Standard mode"):
                coordinator.preview(
                    "new-exam",
                    "question_type",
                    "outdated",
                    stage_ids=["question_type"],
                    list_group_ids=["2026"],
                    question_concurrency=32,
                    speed_mode="fast",
                )


    def test_improvement_report_failure_warns_without_rejecting_validated_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type"],
                )
            )

            coordinator._repository_file_fingerprints = lambda *_args: {}
            with patch(
                "tools.question_review_console.qualification_runs."
                "write_improvement_report",
                side_effect=OSError("report storage unavailable"),
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(completed["receiptValidated"])
        self.assertEqual(completed["queueStatus"], "succeeded")
        self.assertIsNone(completed["improvementReportPath"])
        self.assertIn(
            "report storage unavailable",
            completed["improvementReportWarning"],
        )
        self.assertTrue(result["warning"])
        self.assertIn("改善候補reportを保存できませんでした", result["message"])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])


    def test_provider_gate_retries_batch_then_waits_without_blocking_questions(self):
        class PreparationGateAppServer(FlowAppServer):
            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                raise SubscriptionGateError("利用上限に達しています。")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PreparationGateAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            with self.assertRaisesRegex(QuestionQueuePaused, "回復後に再開"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            report_saved = (root / completed["improvementReportPath"]).is_file()

        self.assertEqual(len(app_server.calls), 4)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertTrue(completed["retrySafe"])
        self.assertEqual(completed["blockedQuestionCount"], 0)
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["queued", "queued"],
        )
        self.assertEqual(completed["pauseKind"], "external_provider")
        self.assertTrue(report_saved)

    def test_provider_gate_recovers_with_client_backoff_before_exhaustion(self):
        class RecoveringGateAppServer(FlowAppServer):
            provider_retry_attempts = 4

            def __init__(self):
                super().__init__()
                self.provider_failures = 0
                self.recovery_attempts = []

            def recover_after_provider_failure(self, *, attempt, emit):
                self.recovery_attempts.append(attempt)
                emit(f"recovered after attempt {attempt}")

            def run_turn(self, prompt, **kwargs):
                if self.provider_failures < 2:
                    self.provider_failures += 1
                    self.calls.append((prompt, kwargs))
                    raise SubscriptionGateError("一時的に利用状況を取得できません。")
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = RecoveringGateAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(app_server.recovery_attempts, [1])
        self.assertEqual(len(app_server.calls), 4)
        self.assertTrue(
            all(
                stage["status"] == "validated"
                for question in completed["questionExecutions"]
                for stage in question["stages"]
            )
        )

    def test_provider_retry_skips_preblocked_question(self):
        class PreparationGateAppServer(FlowAppServer):
            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                raise SubscriptionGateError("利用上限に達しています。")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PreparationGateAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(3),
                ["question_type"],
                app_server=app_server,
            )
            first_question = parent["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                first_question["questionId"],
                "question_type",
                status="blocked",
                error="事前保留",
                block_dependents=True,
            )

            with self.assertRaisesRegex(QuestionQueuePaused, "回復後に再開"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(len(app_server.calls), 4)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "queued", "queued"],
        )


    def test_batch_without_receipt_blocks_each_question_after_deferred_retries(self):
        class ReadOnlyViolationAppServer(FlowAppServer):
            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                return AppServerTurnResult(
                    thread_id="thread-read-only-violation",
                    session_id="session-read-only-violation",
                    turn_id="turn-read-only-violation",
                    final_message="proposal",
                    model="gpt-test",
                    service_tier=None,
                    changed_files=("unexpected.json",),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ReadOnlyViolationAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(app_server.calls), 2)
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsNone(completed["pauseKind"])
        self.assertTrue(completed["retrySafe"])
        self.assertTrue(completed["receiptValidated"])
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "blocked"],
        )


    def test_each_question_prompt_contains_deterministic_identity_and_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            with patch.object(
                coordinator.workflow,
                "prompt",
                side_effect=AssertionError(
                    "一問turnで資格全体を再計画しました。"
                ),
            ):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            batch_prompts = [
                prompt
                for prompt, kwargs in app_server.calls
                if kwargs["work_type"] == "maintenance_question_type_candidate"
            ]
            monitor_contexts = [
                kwargs["monitor_context"]
                for _prompt, kwargs in app_server.calls
                if kwargs["work_type"] == "maintenance_question_type_candidate"
            ]
            aggregate_monitor_contexts = [
                kwargs["monitor_context"]
                for _prompt, kwargs in app_server.aggregate_review_calls
            ]

        self.assertEqual(len(batch_prompts), 2)
        self.assertEqual(len(monitor_contexts), 2)
        self.assertTrue(aggregate_monitor_contexts)
        self.assertTrue(
            all(
                context["runId"] == parent["runId"]
                and context["parentRunId"] == parent["runId"]
                and context["phase"] == "independent_review"
                and context["stageId"] == "question_type"
                for context in aggregate_monitor_contexts
            )
        )
        for context in monitor_contexts:
            self.assertEqual(context["qualification"], "new-exam")
            self.assertEqual(context["runId"], parent["runId"])
            self.assertEqual(context["parentRunId"], parent["runId"])
            self.assertTrue(context["childRunId"])
            self.assertEqual(len(context["questionIds"]), 1)
            self.assertEqual(context["questionId"], context["questionIds"][0])
            self.assertEqual(context["stageId"], "question_type")
            self.assertEqual(
                context["workType"], "maintenance_question_type_candidate"
            )
            self.assertEqual(
                context["phase"], "structured_candidate_generation"
            )
        combined = "\n".join(batch_prompts)
        self.assertIn('"questionId":"new-exam-2026-q1"', combined)
        self.assertIn('"questionId":"new-exam-2026-q2"', combined)
        self.assertIn('"sourceRecordRef":"question_2026_1.json#0"', combined)
        self.assertIn('"sourceRecordRef":"question_2026_2.json#0"', combined)
        self.assertTrue(
            all(prompt.count('"currentRecord":') == 1 for prompt in batch_prompts)
        )
        self.assertTrue(
            all(
                "file、shell、progress、receipt、git、外部状態は変更しない"
                in prompt
                for prompt in batch_prompts
            )
        )

    def test_each_batch_prompt_lists_only_its_own_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(3),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            with patch(
                "tools.question_review_console.qualification_runs."
                "DEFAULT_MAX_QUESTIONS_PER_TURN",
                2,
            ):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            batch_prompts = [
                prompt
                for prompt, kwargs in app_server.calls
                if kwargs["work_type"] == "maintenance_question_type_candidate"
            ]

        self.assertEqual(len(batch_prompts), 3)
        all_source_names = {
            f"question_2026_{index}.json"
            for index in range(1, 4)
        }
        for prompt in batch_prompts:
            questions = PerQuestionQueueAppServer._candidate_questions(prompt)
            self.assertEqual(len(questions), 1)
            source_names = {
                Path(question["sourceIdentity"]["sourceRecordRef"].split("#", 1)[0]).name
                for question in questions
            }
            self.assertIn(f"- 対象問題: `{len(questions)}問`", prompt)
            self.assertEqual(
                {name for name in all_source_names if name in prompt},
                source_names,
            )

    def test_dependency_blocked_item_counts_in_later_phase_and_skips_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type", "question_intent"],
                    app_server=app_server,
                )
            )
            question = parent["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question["questionId"],
                "question_type",
                status="blocked",
                error="前工程で安全に停止した。",
                block_dependents=True,
            )

            with patch(
                "tools.question_review_console.qualification_runs."
                "sync_after_patch_update"
            ) as artifact_sync:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            run = coordinator.store.get("new-exam", parent["runId"])
            later_phase = run["phaseExecutions"][1]

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(later_phase["status"], "partial")
        self.assertEqual(later_phase["blockedCount"], 1)
        self.assertEqual(later_phase["validatedCount"], 0)
        self.assertEqual(run["validatedWorkItemCount"], 0)
        self.assertEqual(run["artifactSync"]["status"], "not_required")
        artifact_sync.assert_not_called()
        self.assertEqual(app_server.calls, [])
        self.assertEqual(synchronizer.calls, [])

    def test_restart_after_validated_save_resumes_from_logical_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type", "question_intent"],
                app_server=FlowAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            original_update_stages = coordinator.store.update_question_stages
            crashed = False

            def crash_after_validated(*args, **kwargs):
                nonlocal crashed
                updated = original_update_stages(*args, **kwargs)
                stage_updates = (
                    args[2]
                    if len(args) >= 3
                    else kwargs.get("updates") or []
                )
                if (
                    not crashed
                    and str(args[1]) == str(parent["runId"])
                    and any(
                        str(value.get("stageId") or "") == "question_type"
                        and (value.get("changes") or {}).get("status")
                        == "validated"
                        for value in stage_updates
                    )
                ):
                    crashed = True
                    raise SystemExit("simulated process stop after validated save")
                return updated

            coordinator.store.update_question_stages = crash_after_validated
            with self.assertRaisesRegex(SystemExit, "after validated save"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            saved = coordinator.store.get("new-exam", parent["runId"])
            saved_stage = saved["questionExecutions"][0]["stages"][0]
            saved_receipt = saved["workVersionReceipt"]

            restarted_store = QualificationRunStore(root)
            restarted_store.recover_interrupted_runs()
            previous = restarted_store.get("new-exam", parent["runId"])
            events = []
            app_server = FlowAppServer(events=events)
            synchronizer = FakeSynchronizer()
            resumed_coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, SourceOnlyInventory()),
                synchronizer,
                DeferredJobs(),
                "secret",
                store=restarted_store,
                app_server=app_server,
            )
            resumed_coordinator._repository_file_fingerprints = lambda *_args: {}
            preview = resumed_coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type", "question_intent"],
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )
            resumed = resumed_coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=previous["runId"],
            )["run"]
            inherited_receipt = resumed["workVersionReceipt"]
            resumed_coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )
            completed = resumed_coordinator.store.get(
                "new-exam", resumed["runId"]
            )

        self.assertTrue(crashed)
        self.assertEqual(saved_stage["status"], "validated")
        self.assertEqual(saved["confirmedGroupIds"], ["2026"])
        self.assertEqual(saved_receipt["recordedCount"], 1)
        self.assertEqual(len(saved_receipt["items"]), 1)
        self.assertEqual(inherited_receipt, saved_receipt)
        writer_event = "session:maintenance_question_intent_candidate"
        self.assertIn(writer_event, events)
        self.assertEqual(app_server.writer_count, 1)
        self.assertGreaterEqual(completed["workVersionReceipt"]["recordedCount"], 1)

    def test_retry_safe_failed_queue_can_resume_blocked_question(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _app_server, previous = (
                self._start_deferred_flow(
                    Path(directory),
                    SourceOnlyInventory(),
                    ["question_type"],
                    app_server=FlowAppServer(),
                )
            )
            question_id = previous["questionExecutions"][0]["questionId"]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question_id,
                "question_type",
                status="blocked",
                error="安全停止後にこの問題だけ再開する。",
            )
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="failed",
                queueStatus="failed",
                retrySafe=True,
                resolvableFailedDeltaPaths=[
                    previous["allowedPatchFiles"][0]
                ],
            )
            coordinator._resolvable_for_plan = lambda *_args, **_kwargs: (
                self.fail("再開時に失敗差分の全履歴を再走査してはいけません")
            )

            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(preview["workItemCount"], 1)


    def test_queue_block_preserves_not_applicable_stage(self):
        cases = (
            ("question_type", ["not_applicable", "blocked"]),
            ("question_intent", ["blocked", "not_applicable"]),
        )
        for terminal_stage_id, expected_statuses in cases:
            with (
                self.subTest(
                    terminal_stage_id=terminal_stage_id,
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                coordinator, _sync, _app_server, parent = (
                    self._start_deferred_flow(
                        root,
                        SourceOnlyInventory(),
                        ["question_type", "question_intent"],
                    )
                )
                question = parent["questionExecutions"][0]
                coordinator.store.update_question_stage(
                    "new-exam",
                    parent["runId"],
                    question["questionId"],
                    terminal_stage_id,
                    status="not_applicable",
                    error=None,
                )
                coordinator._block_remaining_queue(
                    "new-exam", parent["runId"], "停止理由"
                )
                updated = coordinator.store.get("new-exam", parent["runId"])

            self.assertEqual(
                [
                    stage["status"]
                    for stage in updated["questionExecutions"][0]["stages"]
                ],
                expected_statuses,
            )

    def test_phase_start_refreshes_queued_input_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
            )
            phase = parent["phaseExecutions"][0]
            phase_plan, _phase_prompt = coordinator._flow_phase_plan_prompt(
                parent,
                phase,
            )
            target = dict(phase_plan["progressTargets"][0])
            question_id = str(target["id"])
            stage_id = str(phase_plan["stageId"])
            before = coordinator._queue_stage(
                coordinator.store.get("new-exam", parent["runId"]),
                question_id,
                stage_id,
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question_id,
                stage_id,
                status="queued",
                preparationPath="stale-proposal.json",
                preparationHash="stale-hash",
            )
            target["stateHash"] = "current-state-hash"
            phase_plan["policyFingerprints"] = {
                **dict(phase_plan.get("policyFingerprints") or {}),
                stage_id: "current-policy-fingerprint",
            }

            coordinator._refresh_queued_stage_inputs(
                "new-exam",
                parent["runId"],
                phase_plan,
                [target],
                stage_id,
            )
            after = coordinator._queue_stage(
                coordinator.store.get("new-exam", parent["runId"]),
                question_id,
                stage_id,
            )
            expected = input_fingerprint(
                target,
                stage_id,
                "current-policy-fingerprint",
                phase_plan.get("selectedUpdateTargetIds") or [],
            )

        self.assertNotEqual(before["inputFingerprint"], expected)
        self.assertEqual(after["status"], "queued")
        self.assertEqual(after["inputFingerprint"], expected)
        self.assertIsNone(after["preparationPath"])
        self.assertIsNone(after["preparationHash"])

    def test_partial_resume_rejects_retry_unsafe_parent_or_child(self):
        for unsafe_source in ("parent", "child"):
            for operation in ("preview", "start"):
                with (
                    self.subTest(source=unsafe_source, operation=operation),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    coordinator, _sync, _app_server, parent = (
                        self._start_deferred_flow(
                            root,
                            TwoQuestionSourceInventory(),
                            ["question_type"],
                        )
                    )
                    parent = self._mark_parent_partial(coordinator, parent)
                    if unsafe_source == "parent":
                        coordinator.store.update(
                            "new-exam",
                            parent["runId"],
                            retrySafe=False,
                            retryUnsafeReason="親runの再開安全性を確認できません。",
                        )
                    else:
                        self._attach_unsafe_child(coordinator, parent)

                    arguments = {
                        "stage_ids": ["question_type"],
                        "list_group_ids": ["2026"],
                        "resumed_from": parent["runId"],
                    }
                    with self.assertRaisesRegex(
                        QualificationRunError,
                        "再開",
                    ):
                        if operation == "preview":
                            coordinator.preview(
                                "new-exam",
                                "question_type",
                                "outdated",
                                **arguments,
                            )
                        else:
                            coordinator.start(
                                "new-exam",
                                "question_type",
                                "outdated",
                                "stale-preview-token",
                                **arguments,
                            )
                    rejected = coordinator.store.get(
                        "new-exam", parent["runId"]
                    )

                self.assertEqual(rejected["queueStatus"], "partial")
                if unsafe_source == "parent":
                    self.assertFalse(rejected["retrySafe"])
                else:
                    self.assertTrue(rejected["retrySafe"])
                    self.assertIsNone(rejected["unsafeChildRunId"])

    def test_store_restart_propagates_unsafe_child_to_parent_retry_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            phase_plan, _phase_prompt = coordinator._flow_phase_plan_prompt(
                parent,
                parent["phaseExecutions"][0],
            )
            target = phase_plan["progressTargets"][0]
            question_id = str(target["id"])
            child_plan = specialize_question_plan(
                phase_plan,
                question_id,
            )
            child_plan.update(
                kind="human",
                parentRunId=parent["runId"],
                flowPhaseId="question_type",
                phaseIndex=1,
                workType="maintenance_question_type_candidate",
            )
            child = coordinator.store.create_question_attempt(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                child_plan,
                "unsafe child",
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                status="committing",
                childRunIds=[child["runId"]],
                validationAttempts=[
                    {
                        "attempt": 1,
                        "childRunId": child["runId"],
                        "status": "running",
                    }
                ],
                error=None,
            )
            coordinator.store.update(
                "new-exam",
                child["runId"],
                status="running",
                candidateTransactionOpen=True,
                receiptValidated=False,
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="running",
                queueStatus="running",
            )

            restarted_store = QualificationRunStore(root)
            restarted_store.recover_interrupted_runs()
            recovered = restarted_store.get("new-exam", parent["runId"])

        self.assertEqual(recovered["queueStatus"], "partial")
        self.assertFalse(recovered["retrySafe"])
        self.assertEqual(recovered["unsafeChildRunId"], child["runId"])
        self.assertIn("戻せません", recovered["retryUnsafeReason"])

    def test_store_restart_keeps_unstarted_bound_child_retry_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
            )
            phase_plan, _phase_prompt = coordinator._flow_phase_plan_prompt(
                parent,
                parent["phaseExecutions"][0],
            )
            target = phase_plan["progressTargets"][0]
            question_id = str(target["id"])
            child_plan = specialize_question_plan(phase_plan, question_id)
            child_plan.update(
                kind="human",
                parentRunId=parent["runId"],
                flowPhaseId="question_type",
                phaseIndex=1,
                workType="maintenance_question_type_candidate",
            )
            child = coordinator.store.create_question_attempt(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                child_plan,
                "writerはまだ開始していない。",
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="running",
                queueStatus="running",
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                status="preparing",
                childRunIds=[child["runId"]],
                validationAttempts=[
                    {
                        "attempt": 1,
                        "childRunId": child["runId"],
                        "status": "running",
                    }
                ],
                error=None,
            )

            restarted_store = QualificationRunStore(root)
            restarted_store.recover_interrupted_runs()
            recovered = restarted_store.get("new-exam", parent["runId"])
            statuses = {
                question["questionId"]: question["stages"][0]["status"]
                for question in recovered["questionExecutions"]
            }
            resumed_coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, TwoQuestionSourceInventory()),
                synchronizer,
                DeferredJobs(),
                "secret",
                store=restarted_store,
                app_server=FlowAppServer(),
            )
            preview = resumed_coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                resumed_from=recovered["runId"],
            )

        self.assertTrue(recovered["retrySafe"])
        self.assertIsNone(recovered["unsafeChildRunId"])
        self.assertEqual(statuses[question_id], "queued")
        self.assertEqual(
            statuses["new-exam-2026-q2"],
            "queued",
        )
        self.assertTrue(preview["canStart"])
        self.assertEqual(preview["targetCount"], 2)


    def test_replanned_out_of_scope_items_become_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                    app_server=app_server,
                )
            )
            original_phase_plan = coordinator._flow_phase_plan_prompt

            def empty_replanned_phase(parent_run, phase):
                phase_plan, phase_prompt = original_phase_plan(parent_run, phase)
                phase_plan = copy.deepcopy(phase_plan)
                phase_plan.update(
                    targetCount=0,
                    workItemCount=0,
                    progressTargets=[],
                    policyTargets={"question_type": []},
                )
                return phase_plan, phase_prompt

            coordinator._flow_phase_plan_prompt = empty_replanned_phase
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            stages = [
                stage
                for question in run["questionExecutions"]
                for stage in question["stages"]
            ]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual([stage["status"] for stage in stages], [
            "not_applicable",
            "not_applicable",
        ])
        self.assertEqual(
            run["questionExecutionSummary"]["pendingWorkItemCount"],
            0,
        )
        self.assertEqual(run["phaseExecutions"][0]["status"], "skipped")
        self.assertEqual(run["phaseExecutions"][0]["notApplicableCount"], 2)
        self.assertEqual(run["artifactSync"]["status"], "not_required")
        self.assertEqual(app_server.calls, [])
        self.assertEqual(synchronizer.calls, [])

    def test_multi_group_final_sync_excludes_preblocked_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    MultiGroupSourceInventory(),
                    ["question_type", "question_intent"],
                    app_server=FlowAppServer(),
                    group_ids=["2025", "2026"],
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            blocked_question = next(
                question
                for question in parent["questionExecutions"]
                if question["listGroupId"] == "2025"
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                blocked_question["questionId"],
                "question_type",
                status="blocked",
                error="2025は事前条件で保留した。",
                block_dependents=True,
            )

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            questions = {
                question["listGroupId"]: question
                for question in run["questionExecutions"]
            }

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])
        self.assertEqual(
            [stage["status"] for stage in questions["2025"]["stages"]],
            ["blocked", "blocked"],
        )
        self.assertEqual(
            {
                group["listGroupId"]: group["status"]
                for group in run["artifactSync"]["groups"]
            },
            {"2026": "succeeded"},
        )

    def test_later_stage_rechecks_only_question_changed_by_prior_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type", "question_intent"],
                    app_server=app_server,
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._validated_queue_stage_changed = lambda *_args: True
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                "new-exam-2026-q2",
                "question_type",
                status="blocked",
                error="fixtureで対象外",
                block_dependents=True,
            )
            original_phase_plan = coordinator._flow_phase_plan_prompt
            intent_plan_calls = 0

            def phase_plan(parent_run, phase):
                nonlocal intent_plan_calls
                plan, prompt = original_phase_plan(parent_run, phase)
                if phase["id"] != "question_intent":
                    return plan, prompt
                intent_plan_calls += 1
                if intent_plan_calls > 1:
                    return plan, prompt
                plan = copy.deepcopy(plan)
                plan.update(
                    targetCount=0,
                    workItemCount=0,
                    progressTargets=[],
                    policyTargets={"question_intent": []},
                )
                return plan, prompt

            coordinator._flow_phase_plan_prompt = phase_plan

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            q1 = next(
                question
                for question in run["questionExecutions"]
                if question["questionId"] == "new-exam-2026-q1"
            )
            self.assertEqual(result["queueStatus"], "partial")
            self.assertEqual(
                [stage["status"] for stage in q1["stages"]],
                ["validated", "validated"],
            )
            self.assertEqual(intent_plan_calls, 1)
            self.assertEqual(
                app_server.successful_writes,
                [
                    ("new-exam-2026-q1", "question_type"),
                    ("new-exam-2026-q1", "question_intent"),
                ],
            )
            self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])


    def test_changed_question_can_make_later_law_stage_not_applicable(self):
        class ExplainedSourceInventory(SourceOnlyInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                question = group["questions"][0]
                question["projected"] = {
                    **question["projected"],
                    "choiceTextList": ["A"],
                    "correctChoiceText": ["正しい"],
                    "explanationText": [
                        "正しい。法令に関係しない技術事項である。"
                    ],
                    "lawGroundedExplanationNotNeeded": True,
                    "lawReferences": [[]],
                }
                return group

        class NonLawContextAppServer(PerQuestionQueueAppServer):
            @staticmethod
            def _candidate_update(question, stage_id):
                updates = PerQuestionQueueAppServer._candidate_update(
                    question,
                    stage_id,
                )
                if stage_id != "law_context":
                    return updates
                for update in updates:
                    set_fields = {
                        str(value["field"]): value.get("value")
                        for value in update.get("setFields") or []
                    }
                    set_fields.update(
                        isLawRelated=False,
                        lawGroundedExplanationNotNeeded=True,
                        lawReferences=[[]],
                        lawContextForExplanation="技術事項として確認した。",
                    )
                    update["setFields"] = [
                        {
                            "field": field,
                            "value": value,
                        }
                        for field, value in set_fields.items()
                    ]
                return updates

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "lawGroundedExplanationNotNeeded": True,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )
            app_server = NonLawContextAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                ExplainedSourceInventory(),
                ["law_context", "law_audit"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            question = coordinator.workflow.inventory.group(
                "new-exam",
                "2026",
            )["questions"][0]
            law_audit_status = coordinator.work_versions.status_for(
                question,
                [
                    coordinator.workflow.versioned_policies("new-exam")[
                        "law_audit"
                    ]
                ],
            )

        self.assertEqual(
            [stage["status"] for stage in run["questionExecutions"][0]["stages"]],
            ["validated", "not_applicable"],
            run["questionExecutions"][0]["stages"],
        )
        self.assertEqual(
            app_server.successful_writes,
            [("new-exam-2026-q1", "law_context")],
        )
        self.assertEqual(law_audit_status["status"], "current")
        self.assertEqual(run["workVersionReceipt"]["recordedCount"], 2)

    def test_non_applicable_dynamic_stage_keeps_identity_for_version_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type", "law_audit"],
            )
            initial_plan = coordinator.workflow.plan(
                "new-exam",
                "law_audit",
                "needed",
                list_group_id="2026",
            )
            initial_plan.update(
                targetCount=0,
                workItemCount=0,
                targetQuestionKeys=[],
                progressTargets=[],
                targetRecordBindings=[],
                targetRecordAliasGroups=[],
                targetSourceRecordScopes={},
                policyTargets={"law_audit": []},
            )
            coordinator._projection_stage_applicable = (
                lambda _plan, _stage_id, _projected: False
            )

            scoped_plan, writer_target = (
                coordinator._dynamic_question_phase_plan(
                    "new-exam",
                    parent,
                    {"id": "law_audit"},
                    initial_plan,
                    "new-exam-2026-q1",
                )
            )
            specialized = specialize_question_plan(
                scoped_plan,
                "new-exam-2026-q1",
            )

        self.assertIsNone(writer_target)
        self.assertEqual(specialized["targetCount"], 1)
        self.assertEqual(
            specialized["policyTargets"],
            {"law_audit": ["new-exam-2026-q1"]},
        )


    def test_writer_reprepares_only_current_question_when_policy_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            original_policy_check = coordinator._phase_plan_policy_is_current
            policy_check_count = 0

            def policy_is_current(*args, **kwargs):
                nonlocal policy_check_count
                policy_check_count += 1
                if policy_check_count == 2:
                    return False
                return original_policy_check(*args, **kwargs)

            coordinator._phase_plan_policy_is_current = policy_is_current
            messages = []
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                messages.append,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            stage = run["questionExecutions"][0]["stages"][0]
            work_types = [
                kwargs["work_type"]
                for _question, _prompt, kwargs in app_server.calls
            ]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(stage["status"], "validated")
        self.assertEqual(stage["policyRefreshCount"], 1)
        self.assertEqual(
            work_types,
            [
                "maintenance_question_type_candidate",
                "maintenance_question_type_candidate",
            ],
        )
        self.assertTrue(
            any("この問題だけを自動再準備します" in value for value in messages)
        )


    def test_policy_refresh_limit_blocks_only_current_question(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                Path(directory),
                SourceOnlyInventory(),
                ["question_type"],
            )
            question_id = parent["questionExecutions"][0]["questionId"]
            messages = []

            first = coordinator._requeue_policy_changed_question(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                messages.append,
            )
            second = coordinator._requeue_policy_changed_question(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                messages.append,
            )
            third = coordinator._requeue_policy_changed_question(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                messages.append,
            )
            stage = coordinator._queue_stage(
                coordinator.store.get("new-exam", parent["runId"]),
                question_id,
                "question_type",
            )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertFalse(third)
        self.assertEqual(stage["status"], "blocked")
        self.assertEqual(stage["policyRefreshCount"], 2)
        self.assertTrue(any("他の問題は続行します" in value for value in messages))


    def test_all_refresh_modes_skip_non_law_question_in_law_audit(self):
        self.assertFalse(
            QualificationRunCoordinator._projection_stage_applicable(
                {"mode": "group_refresh"},
                "law_audit",
                {"isLawRelated": False},
            )
        )
        self.assertFalse(
            QualificationRunCoordinator._projection_stage_applicable(
                {"mode": "remaining"},
                "law_audit",
                {"isLawRelated": False},
            )
        )

    def test_dynamic_replan_skips_non_law_question_in_law_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            phase_plan = coordinator._plan(
                "new-exam",
                "law_audit",
                "group_refresh",
                None,
                list_group_ids=["2026"],
            )
            parent = {**phase_plan, "mode": "outdated"}

            _plan, target = coordinator._dynamic_question_phase_plan(
                "new-exam",
                parent,
                {"id": "law_audit"},
                phase_plan,
                "new-exam-2026-q1",
            )

        self.assertIsNone(target)

    def test_non_law_without_valid_receipt_returns_to_normal_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                NonLawSourceInventory(),
                ["law_audit"],
            )
            parent = coordinator.store.get("new-exam", parent["runId"])
            phase = parent["phaseExecutions"][0]
            phase_plan, phase_prompt = coordinator._flow_phase_plan_prompt(
                parent,
                phase,
            )
            coordinator._record_work_versions = lambda _plan: (
                (_ for _ in ()).throw(
                    QualificationRunError("監査sidecarがありません")
                )
            )

            spec = coordinator._question_stage_spec(
                "new-exam",
                parent["runId"],
                phase,
                "new-exam-2026-q1",
                phase_plan,
                phase_prompt,
                parent=parent,
            )

        self.assertEqual(spec["status"], "queued")
        self.assertEqual(spec["stageId"], "law_audit")
        self.assertEqual(spec["target"]["id"], "new-exam-2026-q1")

    def test_missing_logical_projection_blocks_only_that_question(self):
        class MissingProjectionInventory(SourceOnlyInventory):
            projected_input = None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                MissingProjectionInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            stage = coordinator.store.get(
                "new-exam", parent["runId"]
            )["questionExecutions"][0]["stages"][0]

        self.assertEqual(stage["status"], "blocked")
        self.assertIn("logicalProjection", stage["error"])
        self.assertEqual(app_server.calls, [])

    def test_resume_does_not_repeat_succeeded_category_scope_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, previous = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["category_setup", "question_set"],
                app_server=app_server,
            )
            question = previous["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_set",
                status="blocked",
                error="question_setだけ再開する。",
            )
            phases = copy.deepcopy(previous["phaseExecutions"])
            phases[0]["status"] = "succeeded"
            phases[1]["status"] = "partial"
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="failed",
                queueStatus="partial",
                phaseExecutions=phases,
            )
            self._write_valid_category(root)
            preview = coordinator.preview(
                "new-exam",
                "category_setup",
                "outdated",
                stage_ids=["category_setup", "question_set"],
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )
            resumed = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=previous["runId"],
            )["run"]

        self.assertEqual(
            [phase["id"] for phase in resumed["phaseExecutions"]],
            ["question_set"],
        )

    def test_fully_succeeded_scope_flow_has_nothing_to_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, previous = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["category_setup", "question_set"],
                app_server=PerQuestionQueueAppServer(),
            )
            question = previous["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_set",
                status="validated",
                error=None,
            )
            phases = copy.deepcopy(previous["phaseExecutions"])
            for phase in phases:
                phase["status"] = "succeeded"
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="succeeded",
                queueStatus="succeeded",
                phaseExecutions=phases,
            )
            self._write_valid_category(root)

            with self.assertRaisesRegex(
                QualificationRunError,
                "再実行が必要な問題はありません",
            ):
                coordinator.preview(
                    "new-exam",
                    "category_setup",
                    "outdated",
                    stage_ids=["category_setup", "question_set"],
                    list_group_ids=["2026"],
                    resumed_from=previous["runId"],
                )

    def test_each_writer_reads_run_local_logical_projection(self):
        listed_source_text = "A  最初の記述。\nB  次の記述。"

        class ProjectingInventory(TwoQuestionSourceInventory):
            def __init__(self):
                self.projected_calls = []

            def projected_input(
                self,
                qualification,
                list_group_id,
                source_record_ref,
            ):
                self.projected_calls.append(source_record_ref)
                question_number = Path(
                    source_record_ref.split("#", 1)[0]
                ).stem.rsplit("_", 1)[1]
                question_id = f"{qualification}-{list_group_id}-q{question_number}"
                return SimpleNamespace(
                    record={
                        "original_question_id": question_id,
                        "sourceQuestionKey": (
                            f"{qualification}:{list_group_id}:q{question_number}"
                        ),
                        "reviewQuestionId": question_id,
                        "sourceRecordRef": source_record_ref,
                        "questionBodyText": listed_source_text,
                        "choiceTextList": ["選択肢A", "選択肢B"],
                        "isCalculationQuestion": True,
                    },
                    applied_files=("output/new-exam/current-patch.json",),
                    errors=(),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = ProjectingInventory()
            held_question_id = "new-exam-2026-q2"
            app_server = PerQuestionQueueAppServer(
                aggregate_review_overrides={
                    "new-exam-2026-q1": {
                        "classification": "target",
                        "decision": "approve",
                    },
                    held_question_id: {
                        "classification": "hold",
                        "decision": "hold",
                        "issueCodes": ["ambiguous_target"],
                    }
                },
                changed_files_by_work_item={
                    ("new-exam-2026-q1", "question_type"): ["candidate"],
                },
            )
            coordinator, _synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    inventory,
                    ["question_type"],
                    app_server=app_server,
                )
            )
            self._write_counted_sources(
                root,
                2,
                question_body_text=listed_source_text,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            original_record_versions = coordinator._record_work_versions

            def record_versions_without_group_scan(run):
                original_group = inventory.group

                def reject_group_scan(*_args, **_kwargs):
                    raise AssertionError(
                        "一問patch toolの工程版記録で年度全体を再構築しました。"
                    )

                inventory.group = reject_group_scan
                try:
                    return original_record_versions(run)
                finally:
                    inventory.group = original_group

            coordinator._record_work_versions = record_versions_without_group_scan

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            children = _question_attempts(
                coordinator.store,
                "new-exam",
                run,
            )
            child = next(
                value
                for value in children
                if str(value["progressTargets"][0]["id"])
                == "new-exam-2026-q1"
            )
            projected_paths = [
                stage["projectedInputPath"]
                for question in run["questionExecutions"]
                for stage in question["stages"]
            ]
            payloads = [
                json.loads((root / path).read_text(encoding="utf-8"))
                for path in projected_paths
            ]

            self.assertEqual(
                set(inventory.projected_calls),
                {"question_2026_1.json#0", "question_2026_2.json#0"},
            )
        self.assertEqual(len(set(projected_paths)), 2)
        review_calls = [
            (question_id, prompt, kwargs)
            for question_id, prompt, kwargs in app_server.aggregate_review_calls
        ]
        self.assertGreaterEqual(len(review_calls), 2)
        review_calls_by_question = {}
        for question_id, prompt, kwargs in review_calls:
            review_calls_by_question.setdefault(question_id, []).append(
                (prompt, kwargs)
            )
        child_question_id = str(child["progressTargets"][0]["id"])
        child_review_calls = review_calls_by_question[child_question_id]
        self.assertEqual(len(child_review_calls), 2)
        self.assertEqual(child_review_calls[0][0], child_review_calls[1][0])
        review_questions = PerQuestionQueueAppServer._candidate_questions(
            child_review_calls[0][0]
        )
        self.assertEqual(
            review_questions[0]["choiceTextList"],
            ["選択肢A", "選択肢B"],
        )
        self.assertNotEqual(
            review_questions[0]["sourceHash"],
            source_text_hash(
                review_questions[0]["questionBodyText"]
                + "".join(review_questions[0]["choiceTextList"])
            ),
        )
        self.assertEqual(len(set(child["aggregateReviewThreadIds"])), 2)
        self.assertEqual(
            [kwargs["model"] for _prompt, kwargs in child_review_calls],
            ["gpt-5.6-luna", "gpt-5.6-luna"],
        )
        self.assertTrue(
            all(
                kwargs["reasoning_effort"] == "high"
                for _prompt, kwargs in child_review_calls
            )
        )
        self.assertEqual(
            [entry["model"] for entry in child["aggregateReviewExecutions"]],
            ["gpt-5.6-luna", "gpt-5.6-luna"],
        )
        self.assertTrue(
            all(entry["reasoningEffort"] == "high" for entry in child["aggregateReviewExecutions"])
        )
        self.assertEqual(
            len({entry["threadId"] for entry in child["aggregateReviewExecutions"]}),
            2,
        )
        self.assertTrue(
            all(
                result["aggregateAnswerReview"]["sourceHash"].startswith("sha256:")
                for value in children
                for result in value["batchQuestionResults"]
            )
        )
        results_by_id = {
            result["questionId"]: result
            for value in children
            for result in value["batchQuestionResults"]
        }
        target_result = results_by_id["new-exam-2026-q1"]
        self.assertEqual(
            target_result["status"],
            "succeeded",
            msg={
                "summary": target_result.get("summary"),
                "commands": target_result.get("commands"),
            },
        )
        self.assertEqual(
            target_result["aggregateAnswerReview"]["decomposition"]["classification"],
            "target",
        )
        held_result = results_by_id[held_question_id]
        self.assertEqual(held_result["status"], "failed")
        self.assertEqual(held_result["changedFiles"], [])
        self.assertEqual(
            held_result["aggregateAnswerReview"]["decomposition"]["decision"],
            "hold",
        )
        self.assertTrue(
            all(
                '"currentRecord":' in prompt
                for _question, prompt, kwargs in app_server.calls
                if kwargs["work_type"] == "maintenance_question_type_candidate"
            )
        )
        self.assertTrue(
            all(
                payload["schemaVersion"] == "question-maintenance-projection/v1"
                for payload in payloads
            )
        )

    def test_hold_deactivates_a_stale_aggregate_target_without_validating_stage(self):
        source_text = "A　原文一。\nB　原文二。"
        newline = source_text.index("\n")
        decomposition = {
            "schemaVersion": "aggregate-answer-decomposition/v1",
            "sourceHash": source_text_hash(source_text),
            "classification": "target",
            "spans": [
                {"start": 0, "end": newline},
                {"start": newline + 1, "end": len(source_text)},
            ],
            "decision": "approve",
            "issueCodes": [],
        }

        class StaleTargetInventory(SourceOnlyInventory):
            def group(self, qualification, list_group_id):
                question_id = f"new-exam-{list_group_id}-q1"
                source = {
                    "original_question_id": question_id,
                    "canonical_question_key": "new-exam:2026:q001",
                    "questionBodyText": source_text,
                    "choiceTextList": ["組合せ1", "組合せ2"],
                    "sourceUniqueKeys": ["source-choice-1", "source-choice-2"],
                    "questionType": "group_choice",
                    "isCalculationQuestion": False,
                }
                projected = {
                    **source,
                    "choiceTextList": ["A　原文一。", "B　原文二。"],
                    "sourceUniqueKeys": ["derived-1", "derived-2"],
                    "aggregateAnswerDecomposition": decomposition,
                }
                return {
                    "listGroupId": list_group_id,
                    "questions": [
                        {
                            "id": question_id,
                            "reviewKey": (
                                f"new-exam:{list_group_id}:"
                                f"question_{list_group_id}_1:{question_id}"
                            ),
                            "qualification": qualification,
                            "listGroupId": list_group_id,
                            "originalQuestionId": question_id,
                            "sourceQuestionKey": "new-exam:2026:q1",
                            "sourceRecordRef": "question_2026_1.json#0",
                            "source": source,
                            "projected": projected,
                            "paths": {
                                "source": (
                                    "output/new-exam/questions_json/2026/00_source/"
                                    "question_2026_1.json"
                                ),
                                "patches": [],
                            },
                            "issues": [],
                            "issueCodes": [],
                            "isLawRelated": False,
                            "workflow": {
                                "merge": "missing",
                                "convert": "missing",
                                "upload": "missing",
                            },
                        }
                    ],
                }

        question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            aggregate_review_overrides={
                question_id: {
                    "classification": "hold",
                    "decision": "hold",
                    "issueCodes": ["ambiguous_target"],
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                StaleTargetInventory(),
                ["question_type"],
                app_server=app_server,
            )

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            child = _question_attempts(
                coordinator.store,
                "new-exam",
                completed,
            )[0]
            held = child["batchQuestionResults"][0]
            patch_path = root / held["changedFiles"][0]
            patch_record = json.loads(patch_path.read_text(encoding="utf-8"))[0]

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(completed["validatedQuestionCount"], 0)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        self.assertEqual(held["status"], "failed")
        self.assertNotIn("workVersionReceipt", held)
        self.assertEqual(patch_record["choiceTextList"], ["組合せ1", "組合せ2"])
        self.assertEqual(
            patch_record["sourceUniqueKeys"],
            ["source-choice-1", "source-choice-2"],
        )
        self.assertNotIn("aggregateAnswerDecomposition", patch_record)

    def test_coordinator_technical_log_proxies_to_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="work",
            )
            coordinator.store.append_technical_log(
                "sample",
                run["runId"],
                {"message": "proxy event"},
            )
            with patch.object(
                coordinator.store,
                "technical_log",
                wraps=coordinator.store.technical_log,
            ) as technical_log:
                result = coordinator.technical_log("sample", run["runId"])

        technical_log.assert_called_once_with("sample", run["runId"])
        self.assertEqual(result["runId"], run["runId"])
        self.assertEqual(result["entries"][0]["message"], "proxy event")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
