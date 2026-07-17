import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.review_store import ReviewStore


class QuestionReviewStoreTests(unittest.TestCase):
    def test_detects_post_fix_and_approval_tracks_current_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root)
            question = {
                "id": "api-id",
                "reviewKey": "sample:2026:file:q1",
                "qualification": "sample-exam",
                "listGroupId": "2026",
                "sourceQuestionKey": "sample:2026:q1",
                "originalQuestionId": "q1",
                "stateHash": "state-1",
                "body": "問題",
                "projected": {"choiceTextList": ["A"], "correctChoiceText": ["正しい"], "explanationText": ["正しい。"]},
                "source": {},
                "uploadReadyDocs": [],
                "paths": {"source": "output/source.json", "patches": []},
            }
            created = store.create(
                question,
                {
                    "note": "確認してほしい",
                    "selection": {
                        "targetLabel": "選択肢1の基本解説",
                        "dataPath": "explanationText[0]",
                        "fields": ["explanationText"],
                        "choiceIndexes": [0],
                        "selectedText": "正しい。",
                    },
                    "investigationScope": "qualification",
                },
            )
            self.assertEqual(created["selection"]["dataPath"], "explanationText[0]")
            self.assertEqual(created["investigationScope"], "qualification")
            self.assertIn("UIで選択した箇所", created["prompt"])
            self.assertIn("選択肢1の基本解説", created["prompt"])
            self.assertIn("> 正しい。", created["prompt"])
            self.assertIn("同じ資格の全フォルダ", created["prompt"])
            question["stateHash"] = "state-2"
            latest = store.latest_for(question)
            self.assertEqual(latest["status"], "post_fix_review")
            persisted = json.loads(Path(latest["reviewPath"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "post_fix_review")

            store.update_status(created["reviewId"], "approved", current_state_hash="state-2")
            latest = store.latest_for(question)

        self.assertEqual(latest["status"], "approved")
        self.assertEqual(latest["snapshots"]["projectedHash"], "state-2")

    def test_qualification_law_audit_prompt_is_compact_and_requires_per_choice_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root)
            question = {
                "id": "api-id",
                "reviewKey": "sample:2026:file:q1",
                "qualification": "sample-exam",
                "listGroupId": "2026",
                "sourceQuestionKey": "sample:2026:law:q1",
                "originalQuestionId": "q1",
                "stateHash": "state-1",
                "body": "正しいものはどれか。",
                "projected": {
                    "choiceTextList": ["条文上の記述A"],
                    "correctChoiceText": ["正しい"],
                    "explanationText": ["正しい。条文どおり。"],
                },
                "source": {},
                "uploadReadyDocs": [],
                "paths": {"source": "output/source.json", "patches": []},
            }
            created = store.create(
                question,
                {
                    "issueTypes": ["law_audit_metadata_incomplete"],
                    "requestKind": "qualification_law_audit",
                    "targetFiles": [
                        "output/sample-exam/questions_json/2025/21_explanationText_added/question_2025_1_explanationText_added.json",
                        "output/sample-exam/questions_json/2026/21_explanationText_added/question_2026_1_explanationText_added.json",
                    ],
                    "targetSourceFiles": [
                        "output/sample-exam/questions_json/2025/00_source/question_2025_1.json",
                        "output/sample-exam/questions_json/2026/00_source/question_2026_1.json",
                    ],
                    "targetRecordAliasGroups": [["q1"], ["q2"]],
                    "fields": ["lawRevisionFacts.current.correctChoiceText"],
                    "note": "監査メタデータを確認してほしい",
                    "selection": {
                        "targetLabel": "法令監査メタデータ",
                        "dataPath": "lawRevisionFacts.current.correctChoiceText",
                        "fields": ["lawRevisionFacts.current.correctChoiceText"],
                        "choiceIndexes": [0],
                        "selectedText": "fieldなし",
                    },
                    "investigationScope": "qualification",
                },
            )

        self.assertEqual(created["requestKind"], "qualification_law_audit")
        self.assertIn("# 法令監査パッチ一括修正", created["prompt"])
        self.assertIn("/2025/21_explanationText_added/", created["prompt"])
        self.assertIn("/2026/21_explanationText_added/", created["prompt"])
        self.assertIn("Codex組み込みweb検索", created["prompt"])
        self.assertIn("一問一肢ずつ", created["prompt"])
        self.assertIn(
            "法令根拠が見つからないこと自体を理由に",
            created["prompt"],
        )
        self.assertIn("確認できなければ`false`を維持", created["prompt"])
        self.assertIn("古い`hold`を残さない", created["prompt"])
        self.assertIn("監査sidecarの`sourceSummary`", created["prompt"])
        self.assertIn(
            str(root / "prompt" / "03b_prompt_audit_current_law_and_patch.md"),
            created["prompt"],
        )
        self.assertIn(
            str(
                root
                / "prompt"
                / "qualification_docs"
                / "sample-exam"
                / "*law_reference*.md"
            ),
            created["prompt"],
        )
        self.assertEqual(len(created["targetSourceFiles"]), 2)
        self.assertEqual(created["targetRecordAliasGroups"], [["q1"], ["q2"]])
        self.assertNotIn("## 問題文", created["prompt"])
        self.assertNotIn("## UIで選択した箇所", created["prompt"])
        self.assertNotIn("## 関連ファイル", created["prompt"])
        self.assertNotIn("review JSON", created["prompt"])
        self.assertNotIn("条文上の記述A", created["prompt"])

    def test_current_question_law_prompt_uses_classification_safety_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root)
            question = {
                "id": "api-id",
                "reviewKey": "sample:2026:file:q1",
                "qualification": "sample-exam",
                "listGroupId": "2026",
                "sourceQuestionKey": "sample:2026:law:q1",
                "originalQuestionId": "q1",
                "stateHash": "state-1",
                "body": "技術知識だけで正誤を判断する問題。",
                "projected": {
                    "choiceTextList": ["技術上の記述A"],
                    "correctChoiceText": ["正しい"],
                    "explanationText": ["正しい。技術上妥当である。"],
                    "isLawRelated": False,
                },
                "source": {},
                "uploadReadyDocs": [],
                "paths": {"source": "output/source.json", "patches": []},
            }
            created = store.create(
                question,
                {
                    "issueTypes": ["manual_review"],
                    "fields": ["isLawRelated"],
                    "note": "法令関連性を再確認する",
                    "investigationScope": "current_question",
                },
            )

        self.assertIn("## 法令関連性の分類安全契約", created["prompt"])
        self.assertIn(
            "法令根拠が見つからないこと自体を理由に",
            created["prompt"],
        )
        self.assertIn("確認できなければ`false`を維持", created["prompt"])
        self.assertIn("古い`hold`を残さない", created["prompt"])
        self.assertIn("監査sidecarの`sourceSummary`", created["prompt"])


if __name__ == "__main__":
    unittest.main()
