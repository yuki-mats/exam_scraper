import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.live_readback_store import LiveReadbackStore


def question(explanation="取得時の解説"):
    document = {
        "questionId": "firestore-document-1",
        "explanationText": explanation,
    }
    return {
        "id": "review-question-1",
        "reviewKey": "sample:2024:q01",
        "qualification": "sample",
        "listGroupId": "2024",
        "stateHash": f"state-{explanation}",
        "uploadReadyDocs": [document],
        "convertedDocs": [document],
    }


class LiveReadbackStoreTests(unittest.TestCase):
    def test_persists_question_result_and_read_time_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored = LiveReadbackStore(root).save(
                question(),
                {
                    "status": "mismatch",
                    "projectId": "production",
                    "expectedSource": "upload-ready",
                    "readAt": "2026-07-12T14:05:00+09:00",
                    "documents": [],
                },
            )
            loaded = LiveReadbackStore(root).load(question())

        self.assertEqual(stored["readbackMeta"]["storedAt"], "2026-07-12T14:05:00+09:00")
        self.assertEqual(loaded["status"], "mismatch")
        self.assertEqual(loaded["readbackMeta"]["storedAt"], "2026-07-12T14:05:00+09:00")
        self.assertFalse(loaded["readbackMeta"]["stale"])

    def test_keeps_previous_result_and_marks_it_stale_after_local_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LiveReadbackStore(Path(directory))
            store.save(
                question(),
                {
                    "status": "match",
                    "projectId": "production",
                    "readAt": "2026-07-12T14:05:00+09:00",
                    "documents": [],
                },
            )
            loaded = store.load(question("更新後の解説"))

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["readbackMeta"]["stale"])
        self.assertEqual(loaded["readbackMeta"]["storedAt"], "2026-07-12T14:05:00+09:00")

    def test_persists_qualification_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LiveReadbackStore(root)
            store.save_manifest(
                "sample",
                {
                    "projectId": "production",
                    "readAt": "2026-07-12T14:05:00+09:00",
                    "groupCount": 3,
                    "questionCount": 120,
                    "documentCount": 480,
                    "statusCounts": {"match": 119, "mismatch": 1},
                    "groups": [],
                },
            )
            loaded = LiveReadbackStore(root).load_manifest("sample")

        self.assertEqual(loaded["storedAt"], "2026-07-12T14:05:00+09:00")
        self.assertEqual(loaded["questionCount"], 120)
        self.assertEqual(loaded["statusCounts"], {"match": 119, "mismatch": 1})


if __name__ == "__main__":
    unittest.main()
