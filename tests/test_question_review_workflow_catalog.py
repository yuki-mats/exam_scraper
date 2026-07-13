import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.canonical_documents import CanonicalDocumentStore
from tools.question_review_console.server import ApiError, QuestionReviewApplication
from tools.question_review_console.workflow_catalog import WorkflowCatalog


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
    def test_production_catalog_is_the_stage_structure_ssot(self):
        catalog = WorkflowCatalog(ROOT).load()

        self.assertEqual(catalog["system"]["name"], "問題整備システム")
        self.assertEqual(
            [stage["id"] for stage in catalog["stages"]],
            [
                "source",
                "setup",
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
        document_paths = {
            catalog["system"]["trunkDocument"],
            *catalog["system"]["defaultDocuments"],
            *catalog["system"]["humanDocuments"],
            *(path for stage in catalog["stages"] for path in stage["documents"]),
        }
        self.assertTrue(all((ROOT / path).is_file() for path in document_paths))
        workflow_source = (
            ROOT / "tools" / "question_review_console" / "qualification_workflow.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("STAGE_CATALOG", workflow_source)

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
