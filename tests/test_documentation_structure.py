import json
import re
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

    def test_console_contract_keeps_recovery_order_and_state_boundary_visible(self):
        text = (
            ROOT / "document" / "operations" / "local_question_review_console.md"
        ).read_text(encoding="utf-8")
        artifact_contract = (
            ROOT / "document" / "operations" / "artifact_contract.md"
        ).read_text(encoding="utf-8")

        self.assertLess(text.index("## 手戻りを防ぐ運用順序"), text.index("## 構成"))
        for value in (
            "receiptValidated=true",
            "artifactSync",
            "パッチ変更を反映",
            "管理機能の`出力`",
            "run中は別作業でfile編集",
            "法令関連問題がすべて現行03b",
        ):
            self.assertIn(value, text)
        for status in (
            "running",
            "deferred",
            "current",
            "not_required",
            "succeeded",
            "blocked",
            "failed",
            "interrupted",
        ):
            self.assertIn(f"| `{status}` |", text)
        for field in (
            "stateHash",
            "policyVersions",
            "policyFingerprints",
            "policyTargets",
        ):
            self.assertIn(f"`{field}`", artifact_contract)
        self.assertLessEqual(max(map(len, text.splitlines())), 500)

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


if __name__ == "__main__":
    unittest.main()
