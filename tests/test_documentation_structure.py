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
            "lawzilla_mcp_question_maintenance_workflow.md",
            "local_question_review_console.md",
            "question_issue_report_workflow.md",
            "../temporary/README.md",
        ):
            self.assertIn(path, text)

    def test_removed_duplicate_documents_do_not_return(self):
        for relative in REMOVED_DUPLICATES:
            self.assertFalse((ROOT / relative).exists(), relative)

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


if __name__ == "__main__":
    unittest.main()
