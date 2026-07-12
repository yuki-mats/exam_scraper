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
        self.assertIn("# 資格単位の法令監査パッチ整備", created["prompt"])
        self.assertIn("表示中の1問だけを直す依頼ではない", created["prompt"])
        self.assertIn("全問題・全選択肢を一問一肢ずつ処理", created["prompt"])
        self.assertIn("e-Govの現行条文本文を実際に開き", created["prompt"])
        self.assertIn("一括コピーや正誤ラベルだけの補完は禁止", created["prompt"])
        self.assertNotIn("## 問題文", created["prompt"])
        self.assertNotIn("## UIで選択した箇所", created["prompt"])
        self.assertNotIn("## 関連ファイル", created["prompt"])
        self.assertNotIn("条文上の記述A", created["prompt"])


if __name__ == "__main__":
    unittest.main()
