import copy
import unittest

from tools.question_review_console.bulk_readback import (
    ScopedFirestoreReadback,
    ScopedReadbackError,
)


def firestore_document(question_id, group_id):
    return {
        "questionId": question_id,
        "qualificationId": "sample-exam",
        "listGroupId": group_id,
        "originalQuestionId": f"source-{question_id}",
        "originalQuestionBodyText": "問題文",
        "originalQuestionChoiceText": "選択肢",
        "correctChoiceText": "正しい",
        "explanationText": "正しい。",
        "questionType": "true_false",
        "questionSetId": "set-1",
    }


class FakeInventory:
    def __init__(self):
        self.groups = {}
        for group_id, question_id in (
            ("2024", "doc-2024"),
            ("2025", "doc-2025"),
            ("general", "doc-general"),
        ):
            document = firestore_document(question_id, group_id)
            self.groups[group_id] = {
                "qualification": "sample-exam",
                "listGroupId": group_id,
                "fingerprint": f"fingerprint-{group_id}",
                "questionCount": 1,
                "questions": [
                    {
                        "id": f"review-{group_id}",
                        "uploadReadyDocs": [document],
                        "convertedDocs": [document],
                    }
                ],
            }

    def inventory(self):
        return {
            "qualifications": [
                {
                    "id": "sample-exam",
                    "listGroupIds": ["2024", "2025", "general"],
                }
            ]
        }

    def group(self, qualification, list_group_id):
        if qualification != "sample-exam":
            raise FileNotFoundError(qualification)
        return self.groups[list_group_id]


class FakeFirestore:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def read_documents(self, document_ids, *, fields=None):
        self.calls.append(list(document_ids))
        self.fields = fields
        return {
            question_id: copy.deepcopy(self.documents[question_id])
            for question_id in document_ids
            if question_id in self.documents
        }


class ScopedFirestoreReadbackTests(unittest.TestCase):
    def setUp(self):
        self.inventory = FakeInventory()
        documents = {
            value["questions"][0]["uploadReadyDocs"][0]["questionId"]: (
                value["questions"][0]["uploadReadyDocs"][0]
            )
            for value in self.inventory.groups.values()
        }
        self.firestore = FakeFirestore(documents)
        self.results = {}
        self.reader = ScopedFirestoreReadback(
            self.inventory,
            self.firestore,
            "secret",
            lambda question_id, result: self.results.__setitem__(question_id, result),
        )

    def test_preview_is_local_and_counts_the_entire_qualification(self):
        preview = self.reader.preview("sample-exam")

        self.assertEqual(preview["groupCount"], 3)
        self.assertEqual(preview["questionCount"], 3)
        self.assertEqual(preview["documentCount"], 3)
        self.assertEqual(preview["scopeLabel"], "資格全体")
        self.assertEqual(preview["listGroupIds"], ["2024", "2025", "general"])
        self.assertEqual(self.firestore.calls, [])

    def test_run_reads_every_group_and_updates_each_question(self):
        preview = self.reader.preview("sample-exam")
        result = self.reader.run(
            "sample-exam",
            preview["previewToken"],
            lambda _: None,
        )

        self.assertEqual(
            self.firestore.calls,
            [["doc-2024", "doc-2025", "doc-general"]],
        )
        self.assertEqual(result["statusCounts"], {"match": 3})
        self.assertEqual(
            set(self.results),
            {"review-2024", "review-2025", "review-general"},
        )
        self.assertTrue(result["readAt"])
        self.assertEqual(
            {value["readAt"] for value in self.results.values()},
            {result["readAt"]},
        )

    def test_rejects_unknown_qualification(self):
        with self.assertRaises(ScopedReadbackError):
            self.reader.preview("unknown")

    def test_rejects_unconfirmed_or_stale_preview(self):
        with self.assertRaises(ScopedReadbackError):
            self.reader.run(
                "sample-exam",
                "invalid-token",
                lambda _: None,
            )


if __name__ == "__main__":
    unittest.main()
