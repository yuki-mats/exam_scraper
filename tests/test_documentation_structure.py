import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRUNK = ROOT / "document" / "operations" / "exam_pipeline_manual_and_automation.md"
REMOVED_DUPLICATES = (
    "document/operations/goal_driven_update_workflow.md",
    "document/operations/ai_patch_execution_prompt_templates.md",
    "document/reference/firestore_datamodel.md",
    "document/operations/lawzilla_mcp_question_maintenance_workflow.md",
    "document/notes",
)
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


class DocumentationStructureTests(unittest.TestCase):
    def test_trunk_is_compact_and_links_to_each_canonical_owner(self):
        text = TRUNK.read_text(encoding="utf-8")

        self.assertLessEqual(len(text.splitlines()), 100)
        for path in (
            "scraping_workflow.md",
            "artifact_contract.md",
            "delivery_workflow.md",
            "../../prompt/README.md",
            "../reference/question_field_contract.md",
            "current_law_question_maintenance_workflow.md",
            "local_question_review_console.md",
            "question_issue_report_workflow.md",
            "../temporary/README.md",
        ):
            self.assertIn(path, text)

        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("document/operations/exam_pipeline_manual_and_automation.md", agents)
        self.assertIn("## 日本語の品質", agents)
        self.assertIn("良質な参考書・問題集の編集水準", agents)
        self.assertIn("事実と根拠を確定する作業と", agents)
        self.assertIn("一度読み直さないと分からない文は書き直す", agents)

    def test_removed_duplicate_documents_do_not_return(self):
        for relative in REMOVED_DUPLICATES:
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_console_contract_prioritizes_operation_and_commit_boundaries(self):
        text = (
            ROOT / "document" / "operations" / "local_question_review_console.md"
        ).read_text(encoding="utf-8")
        artifact_contract = (
            ROOT / "document" / "operations" / "artifact_contract.md"
        ).read_text(encoding="utf-8")

        self.assertLess(text.index("## 手戻りを防ぐ運用順序"), text.index("## 確定、rollback、再生成"))
        for concept in (
            "receiptValidated=true",
            "artifactSync",
            "repository排他",
            "direct_edit_transactions",
            "postCommitErrors",
            "パッチ変更を反映",
            "管理機能の`出力`",
        ):
            self.assertIn(concept, text)
        for field in (
            "stateHash",
            "policyVersions",
            "policyFingerprints",
            "policyTargets",
        ):
            self.assertIn(f"`{field}`", artifact_contract)
        self.assertLessEqual(len(text.splitlines()), 150)

    def test_console_contract_links_transactions_progress_and_observability(self):
        text = (
            ROOT / "document" / "operations" / "local_question_review_console.md"
        ).read_text(encoding="utf-8")
        artifact_contract = (
            ROOT / "document" / "operations" / "artifact_contract.md"
        ).read_text(encoding="utf-8")

        for concept in (
            "開始前bytes",
            "work_versions.json",
            "failed delta",
            "question_started",
            "stage_completed",
            "question_completed",
            "processed",
            "validated",
            "15秒間隔",
            "`heartbeatAt`",
            "technical_log.jsonl",
            "commandStatus",
            "exitCode",
            "outputTail",
            "changedPaths",
            "/technical-log",
        ):
            self.assertIn(concept, text)
        for concept in ("baseline", "work_versions.json", "direct_edit_transactions"):
            self.assertIn(concept, artifact_contract)

    def test_law_audit_sidecar_v2_uses_source_identity(self):
        audit_prompt = (
            ROOT / "prompt/03b_prompt_audit_current_law_and_patch.md"
        ).read_text(encoding="utf-8")
        audit_workflow = (
            ROOT / "document/operations/current_law_question_maintenance_workflow.md"
        ).read_text(encoding="utf-8")

        for concept in (
            "law-revision-audit/v2",
            "`reviewQuestionId`",
            "`sourceQuestionKey`",
            "`sourceRecordRef`",
            "source record",
            "UI",
            "03b",
            "開始",
            "reviewKey",
            "exact join",
            "fail-closed",
            "`progressTargets[].id`",
            "選択肢順の`examTimeDecision`",
            "工程03",
            "日本語",
            "必須metadata",
        ):
            self.assertIn(concept, audit_prompt)
        self.assertNotIn("law-revision-audit/v1", audit_prompt)
        self.assertLessEqual(len(audit_prompt.splitlines()), 140)
        for workflow_summary in (
            "# 現行法監査",
            "../../prompt/03b_prompt_audit_current_law_and_patch.md",
            "../reference/question_field_contract.md",
            "問題文と各選択肢を結合した完全命題",
            "03bを通常整備とは別の新しいsessionで自動実行",
        ):
            self.assertIn(workflow_summary, audit_workflow)

    def test_law_audit_docs_do_not_turn_technical_questions_into_holds(self):
        audit_prompt = (
            ROOT / "prompt" / "03b_prompt_audit_current_law_and_patch.md"
        ).read_text(encoding="utf-8")
        gas_policy = (
            ROOT
            / "prompt"
            / "qualification_docs"
            / "gas-shunin-kou"
            / "01_law_reference_policy.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "法令根拠が見つからないこと自体を理由に、技術問題を",
            audit_prompt,
        )
        self.assertIn(
            '`isLawRelated=false`、`auditStatus="not_law_related"`、`reviewState="secondary_verified"`',
            audit_prompt,
        )
        self.assertIn(
            "法令IDや条項を確認できないという理由だけで",
            gas_policy,
        )
        self.assertIn(
            "付臭、設備操業、防食、地震対策、換気、機器の安全装置",
            gas_policy,
        )

    def test_canonical_document_links_resolve(self):
        documents = [ROOT / "README.md", ROOT / "scripts" / "README.md"]
        documents.extend((ROOT / "document" / "operations").glob("*.md"))
        documents.extend((ROOT / "document" / "reference").glob("*.md"))
        documents.extend((ROOT / "document" / "sources").glob("**/*.md"))
        documents.extend(
            (
                ROOT / "document" / "temporary" / "README.md",
                ROOT / "prompt" / "README.md",
                ROOT / "prompt" / "qualification_docs" / "README.md",
                ROOT / "tools" / "question_bank" / "README.md",
                ROOT / "docs" / "goals" / "README.md",
                ROOT / "docs" / "goals" / "templates" / "manual-patch-quality" / "README.md",
            )
        )

        missing = []
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for raw_target in LINK_PATTERN.findall(text):
                target = raw_target.split("#", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "/")):
                    continue
                resolved = (document.parent / target).resolve()
                if not resolved.exists():
                    missing.append(f"{document.relative_to(ROOT)} -> {target}")

        self.assertEqual(missing, [])

    def test_question_issue_routes_use_stage_02a_prompt(self):
        config = json.loads(
            (ROOT / "config" / "question_issue_reports.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(config["canonicalBranch"], "main")
        self.assertEqual(
            config["promptStageFiles"]["02a"],
            "prompt/02a_prompt_review_correctChoiceText.md",
        )
        self.assertNotIn("23_correctChoiceText_fixed", config["promptStageFiles"])

    def test_scraping_site_registry_covers_configured_scraper_types(self):
        workflow = (ROOT / "document" / "operations" / "scraping_workflow.md").read_text(encoding="utf-8")
        registry = (ROOT / "document" / "sources" / "README.md").read_text(encoding="utf-8")
        presets = json.loads((ROOT / "config" / "scrape_presets.json").read_text(encoding="utf-8"))

        self.assertIn("../sources/README.md", workflow)
        scraper_types = {str(preset.get("scraper_type", "kakomonn")) for preset in presets.values()}
        for scraper_type in scraper_types:
            self.assertIn(f"`{scraper_type}`", registry)

    def test_explanation_prompt_is_compact_without_losing_safety_contracts(self):
        text = (ROOT / "prompt" / "03_prompt_add_explanationText.md").read_text(
            encoding="utf-8"
        )
        field_contract = (
            ROOT / "document" / "reference" / "question_field_contract.md"
        ).read_text(encoding="utf-8")

        self.assertLessEqual(len(text.splitlines()), 300)
        for concept in (
            "事実確定と文章推敲を分けます",
            "`flash_card`と`group_choice`は、選択肢数にかかわらず問題共通の基本解説",
            "`group_choice`は、正答と、比較・組合せ・対応関係を判断する基準",
            "`true_false`は、`explanationText`の要素数を`choiceTextList`と一致",
            "`isCalculationQuestion=true`では、使用する式、数値の代入",
            "用語を選ぶ問題は、選択肢にある各用語の意味と見分け方",
            "`flash_card`と`group_choice`では問題全体の補足だけを対象とし",
            "重複する1件を置くより、0件を正しい結果",
            "`sourceQuestionKey`",
            "`reviewQuestionId`",
            "`sourceRecordRef`",
            "duplicate、unmatched、ambiguous",
            "`updated_to_current_law`",
            "`tertiary_verified`",
            "`hold`",
            "21_explanationText_added/<source_stem>_merged_explanationText_added.json",
            "materialize-patch",
            "check-explanation-patch",
            "question_field_contract.md",
            "artifact_contract.md",
            "qualification_docs/README.md",
        ):
            self.assertIn(concept, text)

        for obsolete in (
            "ソース間矛盾は多数一致",
            "lawAnswerBasis",
            "lawAnswerUpdatedFromExamTime",
            "audit_2nd_class_kenchikushi_law_revision.py",
            "mecnet-kokushi",
        ):
            self.assertNotIn(obsolete, text)

        self.assertIn(
            "`flash_card`と`group_choice`の`explanationText`は、選択肢数にかかわらず問題単位の1要素",
            field_contract,
        )
        self.assertIn(
            "`true_false`だけが選択肢indexと同数の解説",
            field_contract,
        )

    def test_supplement_contract_requires_new_learning_value(self):
        prompt = (ROOT / "prompt" / "03_prompt_add_explanationText.md").read_text(
            encoding="utf-8"
        )
        field_contract = (
            ROOT / "document" / "reference" / "question_field_contract.md"
        ).read_text(encoding="utf-8")
        law_prompt = (
            ROOT / "prompt" / "03b_prompt_audit_current_law_and_patch.md"
        ).read_text(encoding="utf-8")
        console_contract = (
            ROOT / "document" / "operations" / "local_question_review_console.md"
        ).read_text(encoding="utf-8")

        for text in (prompt, field_contract, law_prompt):
            self.assertIn("同じ結論・理由・根拠", text)
            self.assertIn("0件", text)
        self.assertIn("基本解説にない追加情報", prompt)
        self.assertIn("基本解説にない追加情報", field_contract)
        self.assertIn("補足0件は不備ではなく", console_contract)
        self.assertNotIn("「10以上」に10は含まれますか？", prompt)

        qualification_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "prompt" / "qualification_docs").glob("**/*.md")
        )
        self.assertNotRegex(
            qualification_text,
            r"`suggestedQuestions`|`suggestedQuestionDetails`",
        )

        workflow = tomllib.loads(
            (ROOT / "config" / "question_maintenance_workflow.toml").read_text(
                encoding="utf-8"
            )
        )
        stages = {stage["id"]: stage for stage in workflow["stages"]}
        self.assertEqual(stages["explanation"]["policy_version"], "4.0")
        self.assertEqual(stages["law_audit"]["policy_version"], "4.0")


if __name__ == "__main__":
    unittest.main()
