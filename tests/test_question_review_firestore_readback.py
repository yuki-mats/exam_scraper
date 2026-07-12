import unittest

from tools.question_review_console.firestore_readback import (
    FirestoreReadback,
    compare_documents,
    recursive_diff,
)


class FakeSnapshot:
    def __init__(self, question_id, payload):
        self.id = question_id
        self.exists = payload is not None
        self._payload = payload

    def to_dict(self):
        return self._payload


class FakeDocument:
    def __init__(self, question_id):
        self.id = question_id


class FakeCollection:
    def document(self, question_id):
        return FakeDocument(question_id)


class FakeDatabase:
    def __init__(self, documents):
        self.documents = documents

    def collection(self, name):
        if name != "questions":
            raise AssertionError("questions以外を読み取った")
        return FakeCollection()

    def get_all(self, references):
        return [FakeSnapshot(reference.id, self.documents.get(reference.id)) for reference in references]


class QuestionReviewFirestoreReadbackTests(unittest.TestCase):
    def test_recursive_diff_reports_nested_paths(self):
        self.assertEqual(
            recursive_diff({"a": [{"b": 1}]}, {"a": [{"b": 2}]}) ,
            ["a[0].b"],
        )

    def test_compare_reports_missing_and_nested_difference(self):
        expected = [
            {"questionId": "doc1", "lawReferences": [{"article": "1"}]},
            {"questionId": "doc2", "lawReferences": []},
        ]
        result = compare_documents(
            expected,
            {"doc1": {"lawReferences": [{"article": "2"}]}},
            fields=("lawReferences",),
        )
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["missingDocumentIds"], ["doc2"])
        self.assertIn("doc1.lawReferences[0].article", result["differences"])

    def test_reader_fetches_only_expected_document_ids(self):
        database = FakeDatabase({"doc1": {"correctChoiceText": "正しい"}, "other": {}})
        reader = FirestoreReadback(db_factory=lambda: database)
        result = reader.read_question(
            {
                "uploadReadyDocs": [
                    {"questionId": "doc1", "correctChoiceText": "正しい"}
                ]
            }
        )
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["documentCount"], 1)


if __name__ == "__main__":
    unittest.main()
