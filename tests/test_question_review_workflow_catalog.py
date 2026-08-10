import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.canonical_documents import CanonicalDocumentStore
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.server import ApiError, QuestionReviewApplication
from tools.question_review_console.workflow_catalog import (
    AGENT_POLICY_MODELS,
    AGENT_POLICY_REASONING_EFFORTS,
    WorkflowCatalog,
    normalize_policy_version,
)
from tools.question_review_console.codex_app_server import (
    QUESTION_MAINTENANCE_MODELS,
    TURN_REASONING_EFFORT,
)


ROOT = Path(__file__).resolve().parents[1]


def catalog_text(name: str, purpose: str) -> str:
    return f'''[system]
id = "question-maintenance-control-center"
name = "{name}"
description = "test"
trunk_document = "document/operations/guide.md"
default_documents = []

[[stages]]
id = "source"
code = "00"
label = "取得"
purpose = "{purpose}"
kind = "source"
documents = ["document/operations/guide.md"]

[[stages]]
id = "setup"
code = "準備"
label = "資格方針"
purpose = "方針"
kind = "human"
documents = []

[[stages]]
id = "law_context"
code = "02b"
label = "法令根拠"
purpose = "根拠"
kind = "human"
patch_dir = "18_law_context_prepared"
patch_suffix = "lawContext_prepared"
documents = []

[[stages]]
id = "law_audit"
code = "03b"
label = "現行法監査"
purpose = "監査"
kind = "human"
documents = []

[[stages]]
id = "category_setup"
code = "03c"
label = "カテゴリ設計"
purpose = "分類"
kind = "human"
documents = []

[[stages]]
id = "delivery"
code = "出力"
label = "公開準備"
purpose = "出力"
kind = "machine"
documents = []
'''


class WorkflowCatalogTests(unittest.TestCase):
    def test_qualification_data_loads_groups_concurrently_and_preserves_order(self):
        class ParallelInventory:
            def __init__(self):
                self.lock = threading.Lock()
                self.release = threading.Event()
                self.active = 0
                self.peak = 0

            @staticmethod
            def inventory():
                return {
                    "qualifications": [
                        {
                            "id": "sample-exam",
                            "listGroupIds": ["2025", "2026"],
                        }
                    ]
                }

            def group(self, qualification, group_id):
                with self.lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                    if self.active == 2:
                        self.release.set()
                try:
                    self.release.wait(1)
                    return {
                        "qualification": qualification,
                        "listGroupId": group_id,
                        "questions": [],
                    }
                finally:
                    with self.lock:
                        self.active -= 1

        with tempfile.TemporaryDirectory() as directory:
            workflow = object.__new__(QualificationWorkflow)
            workflow.repo_root = Path(directory)
            workflow.inventory = ParallelInventory()

            groups, questions = workflow._qualification_data("sample-exam")

        self.assertEqual(
            [group["listGroupId"] for group in groups],
            ["2025", "2026"],
        )
        self.assertEqual(questions, [])
        self.assertEqual(workflow.inventory.peak, 2)

    def test_prompt_from_plan_does_not_rebuild_the_validated_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = object.__new__(QualificationWorkflow)
            workflow.repo_root = Path(directory)
            stage_plan = {
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "stageCode": "03",
                "stageLabel": "解説",
                "targetCount": 1,
            }
            plan = {
                "qualification": "sample-exam",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "stageCode": "03",
                "stageLabel": "解説",
                "kind": "human",
                "mode": "outdated",
                "modeLabel": "未整備",
                "targetCount": 1,
                "workItemCount": 1,
                "lawWorkflowEnabled": False,
                "canonicalDocs": [],
                "sourceFiles": [],
                "outputFiles": [],
                "stagePlans": [stage_plan],
            }

            with patch.object(
                workflow,
                "plan_many",
                side_effect=AssertionError("planを再計算してはいけません"),
            ):
                rendered = workflow.prompt_from_plan(plan)

        self.assertEqual(rendered["targetCount"], 1)
        self.assertIn("03 解説（1件）", rendered["prompt"])

    def test_agent_policy_allowlists_match_app_server_turn_contract(self):
        self.assertEqual(AGENT_POLICY_MODELS, set(QUESTION_MAINTENANCE_MODELS))
        self.assertEqual(AGENT_POLICY_REASONING_EFFORTS, {TURN_REASONING_EFFORT})

    def test_question_type_policy_describes_dual_review_before_candidate(self):
        prompt = (ROOT / "prompt/01_prompt_fix_questionType.md").read_text(
            encoding="utf-8"
        )
        operations = (
            ROOT / "document/operations/local_question_review_console.md"
        ).read_text(encoding="utf-8")

        first_review = prompt.index("専用レビューを2回実行")
        consensus = prompt.index("serverが二者の結果を照合", first_review)
        candidate = prompt.index("通常の問題形式候補", consensus)
        self.assertLess(first_review, consensus)
        self.assertLess(consensus, candidate)
        self.assertIn("問題単位の`hold`", prompt)
        self.assertIn("詳細schemaはproductionコードを正本", operations)
        self.assertIn("通常の問題形式候補を生成", operations)
        self.assertIn("問題単位の`hold`", operations)

    def test_question_type_policy_distinguishes_choice_comparison_from_recall(self):
        prompt = (ROOT / "prompt/01_prompt_fix_questionType.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "`choiceTextList`の各肢が互いに異なる条件、物質、反応式などの計算対象を持ち",
            prompt,
        )
        self.assertIn(
            "比較対象の反応式そのものを持つ場合は",
            prompt,
        )
        self.assertIn("各反応式の該当・非該当を判定する`true_false`", prompt)
        self.assertIn(
            "計算で一つの数値を求め、その値に最も近い数値候補を選ぶ問題も",
            prompt,
        )
        self.assertIn(
            "単一の計算結果に数値候補を順番に当てはめるだけの問題は",
            prompt,
        )

    def test_law_audit_keeps_official_answer_result_read_only(self):
        catalog = WorkflowCatalog(ROOT).load()
        law_audit = next(
            stage for stage in catalog["stages"] if stage["id"] == "law_audit"
        )
        target = next(
            item
            for item in law_audit["updateTargets"]
            if item["id"] == "law_audit"
        )
        prompt = (
            ROOT / "prompt/03b_prompt_audit_current_law_and_patch.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("answer_result_text", target["fields"])
        self.assertIn("answer_result_text", target["readFields"])
        self.assertIn("取得元の公式解答を保持するread-only field", prompt)

    def test_law_context_prompt_uses_only_its_stage_validator(self):
        prompt = (ROOT / "prompt/02b_prompt_prepare_law_context.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("check-law-context-patch", prompt)
        self.assertNotIn("question_bank.py quality-gate", prompt)

    def test_question_set_policy_uses_complete_question_and_canonical_taxonomy(self):
        prompt = (ROOT / "prompt/04_prompt_link_questionSetId.md").read_text(
            encoding="utf-8"
        )

        required_rules = (
            "`questionBodyText`と`choiceTextList`の全選択肢を最初に通読",
            "`questionBodyText`だけで仮分類してはいけません",
            "対象資格の正本文書",
            "最も近いIDへ強制しません",
            "03cで`category.json`と資格別正本を再作業",
            "問題単位の`hold`",
            "category.json所属を含むパッチ検証",
            "すべての`questionSetId`が`category.json`の`questionSets[]`に存在",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)

        self.assertNotIn("g1_09_keikaku_ippan", prompt)
        self.assertNotIn("### 建築計画", prompt)

    def test_question_set_policy_distinguishes_question_and_choice_level_fields(self):
        prompt = (ROOT / "prompt/04_prompt_link_questionSetId.md").read_text(
            encoding="utf-8"
        )
        contract = (ROOT / "document/reference/question_field_contract.md").read_text(
            encoding="utf-8"
        )

        for text in (
            "`questionSetId`は、問題全体の主な復習先",
            "`questionSetIdList`は、Firestore由来の複数の設問",
            "`choiceQuestionSetIds`は、`choiceTextList`と同じ順序・件数",
            "通常の04では`questionSetId`だけを再判定",
            "3 fieldを互いに自動変換せず",
        ):
            with self.subTest(text=text):
                self.assertIn(text, prompt)

        self.assertIn("### 04の分類field", contract)
        self.assertIn("肢別分類とは別に確定する", contract)

    def test_originalization_policy_preserves_quality_with_minimum_edits(self):
        prompt = (ROOT / "prompt/05_prompt_originalize_question.md").read_text(
            encoding="utf-8"
        )
        operations = (
            ROOT / "document/operations/original_question_authoring_workflow.md"
        ).read_text(encoding="utf-8")
        clf_profile = (
            ROOT
            / "prompt/qualification_docs/aws-cloud-practitioner/01_exam_profile.md"
        ).read_text(encoding="utf-8")
        saa_profile = (
            ROOT
            / "prompt/qualification_docs/aws-solutions-architect-associate/01_exam_profile.md"
        ).read_text(encoding="utf-8")
        aws_samples = (
            ROOT
            / "prompt/qualification_docs/aws_official_japanese_sample_questions.md"
        ).read_text(encoding="utf-8")

        for text in (prompt, operations):
            self.assertIn("作り替え", text)
            self.assertIn("一つ又は必要最小限", text)
            self.assertIn("誤答", text)
            self.assertIn("難易度", text)
            self.assertIn("図表問題のまま維持", text)
        self.assertIn("問題文を維持して選択肢だけを変えてもよい", prompt)
        self.assertIn("選択肢の順番", prompt)
        self.assertIn("正答が成立する理由", prompt)
        self.assertIn("各誤答が誤りである理由", prompt)
        self.assertIn("正答と解説に照らし", prompt)
        self.assertIn("元の問題文、選択肢、正答、解説を一つの基準セット", prompt)
        self.assertIn("問題文と選択肢は局所的な微修正", operations)
        explanation_prompt = (
            ROOT / "prompt/03_prompt_add_explanationText.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`originalizationSource`も読み", explanation_prompt)
        self.assertIn("正答理由と各誤答理由を保った独自解説へ再構成", explanation_prompt)
        for profile in (clf_profile, saa_profile):
            self.assertIn("独自問題化で維持するAWSらしさ", profile)
            self.assertIn("AWSサービス名", profile)
            self.assertIn("公式日本語サンプル問題", profile)
            self.assertIn("英語から翻訳されたAWS公式試験", profile)
        self.assertIn("ポーリングリクエスト", aws_samples)
        self.assertIn("レイテンシー", aws_samples)
        self.assertIn("これらの要件を満たすアプローチ", aws_samples)
        self.assertIn("独自問題化の変更前・変更後サンプル", aws_samples)
        self.assertIn("問題ID: `91543`", aws_samples)
        self.assertIn(
            "AWS WAFの特徴は次のうちどれか。（2つ選択）",
            aws_samples,
        )
        self.assertIn(
            "AWSサポートプランの「ベーシックサポート」について正しい説明はどれか。",
            aws_samples,
        )
        self.assertIn("ある企業は、SNS上での自社製品", aws_samples)
        self.assertIn("バッチ処理システムを構築した", aws_samples)
        self.assertIn("正しい組み合わせを選べ。", aws_samples)
        self.assertIn("世界各地のAWSデータセンター", aws_samples)
        self.assertIn("インバウンドとアウトバウンド", aws_samples)
        self.assertNotIn("不適切な変更:", aws_samples)

        aws_catalog = QualificationWorkflow(ROOT, None).catalog(
            "aws-cloud-practitioner"
        )
        originalize = next(
            stage
            for stage in aws_catalog["stages"]
            if stage["id"] == "originalize"
        )
        self.assertIn(
            "prompt/qualification_docs/aws_official_japanese_sample_questions.md",
            originalize["canonicalDocs"],
        )
        clf_readme = "prompt/qualification_docs/aws-cloud-practitioner/README.md"
        self.assertIn(clf_readme, originalize["canonicalDocs"])
        self.assertIn("question_ping-t-76_39674.json#0", (ROOT / clf_readme).read_text(encoding="utf-8"))

    def test_production_catalog_is_the_stage_structure_ssot(self):
        catalog = WorkflowCatalog(ROOT).load()

        self.assertEqual(catalog["system"]["name"], "問題整備システム")
        self.assertEqual(
            catalog["system"]["humanDocuments"],
            ["AGENTS.md", "prompt/README.md"],
        )
        self.assertEqual(
            [stage["id"] for stage in catalog["stages"]],
            [
                "source",
                "setup",
                "originalize",
                "question_type",
                "question_intent",
                "correct_choice",
                "law_context",
                "explanation",
                "law_audit",
                "category_setup",
                "question_set",
                "delivery",
            ],
        )
        self.assertFalse(
            next(
                stage for stage in catalog["stages"] if stage["id"] == "originalize"
            )["automatic"]
        )
        self.assertEqual(
            catalog["sessionGroups"],
            [
                {"id": "maintenance", "label": "問題を整備"},
                {"id": "law_audit", "label": "現行法を監査"},
                {"id": "question_set", "label": "問題集を整備"},
            ],
        )
        self.assertEqual(
            [
                stage["sessionGroup"]
                for stage in catalog["stages"]
                if stage.get("batchSelectable")
            ],
            [
                "maintenance",
                "maintenance",
                "maintenance",
                "maintenance",
                "maintenance",
                "maintenance",
                "law_audit",
                "question_set",
                "question_set",
            ],
        )
        document_paths = {
            catalog["system"]["trunkDocument"],
            *catalog["system"]["defaultDocuments"],
            *catalog["system"]["humanDocuments"],
            *(path for stage in catalog["stages"] for path in stage["documents"]),
        }
        self.assertTrue(all((ROOT / path).is_file() for path in document_paths))
        versioned = [
            stage
            for stage in catalog["stages"]
            if stage.get("batchSelectable")
            and stage.get("policyVersion") is not None
        ]
        self.assertTrue(versioned)
        version_by_stage = {
            stage["id"]: stage["policyVersion"] for stage in versioned
        }
        stage_by_id = {stage["id"]: stage for stage in catalog["stages"]}
        owned_fields = {
            stage_id: {
                field
                for target in stage_by_id[stage_id]["updateTargets"]
                for field in target["fields"]
            }
            for stage_id in (
                "question_type",
                "question_intent",
                "correct_choice",
                "question_set",
            )
        }
        self.assertEqual(
            owned_fields["question_type"],
            {"questionType", "isCalculationQuestion"},
        )
        self.assertEqual(owned_fields["question_intent"], {"questionIntent"})
        self.assertEqual(
            owned_fields["correct_choice"],
            {"correctChoiceText"},
        )
        self.assertEqual(owned_fields["question_set"], {"questionSetId"})
        self.assertEqual(version_by_stage["explanation"], "10.1")
        self.assertEqual(version_by_stage["law_audit"], "4.5")
        self.assertEqual(version_by_stage["law_context"], "1.4")
        self.assertEqual(version_by_stage["originalize"], "2.9")
        self.assertEqual(version_by_stage["question_type"], "6.0")
        self.assertEqual(version_by_stage["question_intent"], "4.0")
        self.assertEqual(version_by_stage["correct_choice"], "4.0")
        self.assertEqual(version_by_stage["question_set"], "2.0")
        self.assertEqual(
            stage_by_id["question_type"]["agentPolicy"]["independent_review"],
            {"model": "gpt-5.6-luna", "reasoningEffort": "high"},
        )
        self.assertTrue(
            all(
                version == "1.0"
                for stage_id, version in version_by_stage.items()
                if stage_id
                not in {
                    "originalize",
                    "explanation",
                    "law_audit",
                    "law_context",
                    "question_type",
                    "question_intent",
                    "correct_choice",
                    "question_set",
                }
            )
        )
        self.assertEqual(catalog["evaluation"]["policyVersion"], "5.1")
        explanation_targets = {
            target["selectionId"]: target
            for target in stage_by_id["explanation"]["updateTargets"]
        }
        self.assertEqual(
            explanation_targets["explanation.learning_pattern"]["fields"],
            ["questionLearningPatternId"],
        )
        self.assertIn(
            "分類",
            explanation_targets["explanation.learning_pattern"][
                "instructionAliases"
            ],
        )
        self.assertEqual(
            explanation_targets["explanation.supplementary_questions"]["fields"],
            ["suggestedQuestionDetailsByChoice"],
        )
        self.assertIn(
            "explanationText",
            explanation_targets["explanation.supplementary_questions"]["readFields"],
        )
        writable_fields = [
            field
            for stage in catalog["stages"]
            for target in stage["updateTargets"]
            for field in target["fields"]
        ]
        self.assertNotIn("suggestedQuestions", writable_fields)
        self.assertFalse(stage_by_id["category_setup"]["supportsGroupScope"])
        self.assertTrue(stage_by_id["question_set"]["supportsGroupScope"])
        self.assertTrue(stage_by_id["delivery"]["supportsGroupScope"])
        self.assertTrue(
            all((ROOT / path).is_file() for path in catalog["evaluation"]["inputs"])
        )
        workflow_source = (
            ROOT / "tools" / "question_review_console" / "qualification_workflow.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("STAGE_CATALOG", workflow_source)

    def test_policy_version_is_a_major_minor_string(self):
        self.assertEqual(normalize_policy_version("1.10"), "1.10")
        self.assertEqual(normalize_policy_version(1), "1.0")
        for invalid in (1.1, "v1.0", "1", True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    normalize_policy_version(invalid)

    def test_agent_policy_rejects_unknown_roles_and_incomplete_settings(self):
        invalid_policies = (
            'agent_policy = { writer = { model = "gpt-5.6-luna", reasoning_effort = "high" } }',
            'agent_policy = { independent_review = { model = "gpt-5.6-luna" } }',
        )
        for policy in invalid_policies:
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "config/question_maintenance_workflow.toml"
                config.parent.mkdir(parents=True)
                config.write_text(
                    catalog_text("test", "取得").replace(
                        'documents = ["document/operations/guide.md"]',
                        'documents = ["document/operations/guide.md"]\n' + policy,
                        1,
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    WorkflowCatalog(root).load()

    def test_catalog_changes_are_loaded_without_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "question_maintenance_workflow.toml"
            config.parent.mkdir(parents=True)
            config.write_text(catalog_text("最初の名前", "最初の目的"), encoding="utf-8")
            store = WorkflowCatalog(root)

            first = store.load()
            config.write_text(catalog_text("更新後の名前", "更新後の目的"), encoding="utf-8")
            second = store.load()

        self.assertEqual(first["system"]["name"], "最初の名前")
        self.assertEqual(second["system"]["name"], "更新後の名前")
        self.assertEqual(second["stages"][0]["purpose"], "更新後の目的")
        self.assertNotEqual(first["catalogHash"], second["catalogHash"])

    def test_invalid_live_catalog_keeps_last_known_good_until_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "question_maintenance_workflow.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                catalog_text("正常な名前", "正常な目的"),
                encoding="utf-8",
            )
            store = WorkflowCatalog(root)
            first = store.load()

            config.write_text("broken = [\n", encoding="utf-8")
            fallback = store.load()

            config.write_text(
                catalog_text("復旧後の名前", "復旧後の目的"),
                encoding="utf-8",
            )
            recovered = store.load()

        self.assertEqual(fallback["catalogHash"], first["catalogHash"])
        self.assertEqual(fallback["stages"], first["stages"])
        self.assertTrue(fallback["restartRequired"])
        self.assertIn("直前の正常な設定", fallback["catalogWarning"])
        self.assertEqual(recovered["system"]["name"], "復旧後の名前")
        self.assertFalse(recovered["restartRequired"])
        self.assertEqual(recovered["catalogWarning"], "")

    def test_explanation_policy_uses_shared_patterns_and_fact_then_difference_order(self):
        prompt = (ROOT / "prompt" / "03_prompt_add_explanationText.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("正しい内容と条文位置 → 選択肢との差", prompt)
        for pattern in (
            "用語と基本を押さえる",
            "違いを整理する",
            "条件を押さえる",
            "例外まで押さえる",
            "しくみと理由を理解する",
            "順序と流れをつかむ",
            "事例に当てはめて考える",
            "計算して答えを求める",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(f"| {pattern} |", prompt)
                self.assertIn(f"**{pattern}**", prompt)
        self.assertIn("主パターンを一つ選びます", prompt)
        self.assertIn("複合パターンは増やさず", prompt)
        self.assertIn("`questionLearningPatternId`へ保存します", prompt)
        self.assertIn("`questionSetId`は「どの単元を復習するか」", prompt)
        self.assertIn("学習者の苦手", prompt)
        self.assertIn("復習すべき単元", prompt)
        self.assertIn("条件を満たすと通常とは違う扱い", prompt)
        self.assertIn("元の設問が複数の記述をまとめていても", prompt)
        self.assertIn("各工程の役割と次へ進める理由", prompt)
        self.assertIn("question_learning_patterns.json", prompt)
        self.assertIn("`間違い。正しくは、〜。`", prompt)
        self.assertIn("問題単位の解説は`正しい。`", prompt)
        self.assertIn("正しい記述は本文だけで完結していれば`正しい。`", prompt)
        self.assertIn("中学生でも意味を追える言葉", prompt)
        self.assertIn("公式を一般形で先に示し", prompt)
        self.assertIn("すべての変数を初学者にも分かる日本語", prompt)
        self.assertIn("法令名、法令番号、条・項・号", prompt)
        self.assertNotIn("conditions_exceptions", prompt)
        self.assertIn("qualification_docs/README.md", prompt)
        self.assertIn("資格別`README.md`に「解説の資格固有調整」", prompt)
        self.assertIn("共通ルールを保ったまま", prompt)
        self.assertNotIn("02_explanation_strategy.md", prompt)

    def test_aws_explanation_stage_reads_approved_learning_pattern_examples(self):
        relative = "prompt/qualification_docs/aws-cloud-practitioner/README.md"
        document = (ROOT / relative).read_text(encoding="utf-8")
        catalog = QualificationWorkflow(ROOT, None).catalog(
            "aws-cloud-practitioner"
        )
        explanation = next(
            stage for stage in catalog["stages"] if stage["id"] == "explanation"
        )

        self.assertIn(relative, explanation["canonicalDocs"])
        self.assertIn("2026年7月30日から新規顧客の受付を停止", document)
        self.assertIn("既存顧客にはバグ修正とセキュリティ更新を含むフルサポート", document)
        self.assertIn("Amazon Bedrock Managed Knowledge Base", document)
        self.assertIn("SlackとDropboxのネイティブコネクタがない", document)
        for pattern_id, source_ref in (
            ("terms_basics", "question_ping-t-76_39411.json#0"),
            ("differences_relationships", "question_ping-t-76_39578.json#0"),
            ("conditions_scope", "question_keepitup-CLF203C040.json#0"),
            ("principles_exceptions", "question_ping-t-76_39629.json#0"),
            ("mechanisms_reasons", "question_keepitup-CLF201C028.json#0"),
            ("sequence_flow", "question_ping-t-76_39640.json#0"),
            ("scenario_application", "question_ping-t-76_39400.json#0"),
            ("calculation", "question_keepitup-CLF201C044.json#0"),
        ):
            with self.subTest(pattern_id=pattern_id):
                self.assertIn(f"`{pattern_id}`", document)
                self.assertIn(f"`{source_ref}`", document)
        self.assertEqual(document.count("分類の判断理由:"), 8)
        self.assertEqual(document.count("解説の構成:"), 8)
        self.assertIn("230TBのデータを、1台210TB", document)
        self.assertIn("1台では20TB不足する", document)
        self.assertNotIn("1台では10TB不足する", document)
        self.assertIn("正解になる選択肢がない場合は03を`blocked`", document)
        self.assertIn("05と02aへ戻します", document)


class CanonicalDocumentTests(unittest.TestCase):
    def test_document_content_and_hash_refresh_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "document" / "operations" / "guide.md"
            path.parent.mkdir(parents=True)
            path.write_text("# 最初の正本\n", encoding="utf-8")
            store = CanonicalDocumentStore(root)

            first = store.read("document/operations/guide.md")
            path.write_text("# 更新後の正本\n\n本文\n", encoding="utf-8")
            second = store.read("document/operations/guide.md")

        self.assertEqual(first["title"], "最初の正本")
        self.assertEqual(second["title"], "更新後の正本")
        self.assertNotEqual(first["contentHash"], second["contentHash"])

    def test_document_api_blocks_noncanonical_and_traversal_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide = root / "document" / "operations" / "guide.md"
            guide.parent.mkdir(parents=True)
            guide.write_text("# ガイド\n", encoding="utf-8")
            temporary = root / "document" / "temporary" / "audit.md"
            temporary.parent.mkdir(parents=True)
            temporary.write_text("# 一時監査\n", encoding="utf-8")
            app = QuestionReviewApplication(root)

            status, payload = app.get(
                "/api/document", {"path": ["document/operations/guide.md"]}
            )
            with self.assertRaises(ApiError):
                app.get("/api/document", {"path": ["document/temporary/audit.md"]})
            with self.assertRaises(ApiError):
                app.get("/api/document", {"path": ["../outside.md"]})

        self.assertEqual(status, 200)
        self.assertEqual(payload["title"], "ガイド")

    def test_static_ui_exposes_live_workflow_guide(self):
        html = (
            ROOT / "tools" / "question_review_console" / "static" / "index.html"
        ).read_text(encoding="utf-8")
        javascript = (
            ROOT / "tools" / "question_review_console" / "static" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("問題整備システム", html)
        for element_id in (
            "qualification-workflow-guide",
            "workflow-guide",
            "workflow-guide-documents",
            "workflow-guide-content",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("/api/workflow-catalog", javascript)
        self.assertIn("/api/document", javascript)
        self.assertIn("refreshWorkflowGuideDocuments", javascript)
        self.assertIn("window.setInterval(refreshWorkflowGuide, 2000)", javascript)


if __name__ == "__main__":
    unittest.main()
