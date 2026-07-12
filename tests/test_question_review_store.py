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
            created = store.create(question, {"note": "確認してほしい"})
            question["stateHash"] = "state-2"
            latest = store.latest_for(question)
            self.assertEqual(latest["status"], "post_fix_review")
            persisted = json.loads(Path(latest["reviewPath"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "post_fix_review")

            store.update_status(created["reviewId"], "approved", current_state_hash="state-2")
            latest = store.latest_for(question)

        self.assertEqual(latest["status"], "approved")
        self.assertEqual(latest["snapshots"]["projectedHash"], "state-2")


if __name__ == "__main__":
    unittest.main()
