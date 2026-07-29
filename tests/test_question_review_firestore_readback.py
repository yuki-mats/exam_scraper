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

    def get_all(self, references, field_paths=None):
        self.field_paths = field_paths
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

    def test_compare_preserves_omitted_optional_fields_for_regular_documents(self):
        result = compare_documents(
            [{"questionId": "doc1", "isChoiceOnly": False}],
            {
                "doc1": {
                    "suggestedQuestions": ["既存の補足質問"],
                    "suggestedQuestionDetails": [
                        {"question": "既存の補足質問", "answer": "既存の回答"}
                    ],
                }
            },
            fields=("suggestedQuestions", "suggestedQuestionDetails"),
        )

        self.assertEqual(result["status"], "match")
        self.assertEqual(result["differences"], [])

    def test_compare_requires_choice_only_omitted_fields_to_be_deleted(self):
        result = compare_documents(
            [{"questionId": "doc1", "isChoiceOnly": True}],
            {
                "doc1": {
                    "suggestedQuestions": ["削除対象の補足質問"],
                    "suggestedQuestionDetails": [
                        {"question": "削除対象の補足質問", "answer": "削除対象の回答"}
                    ],
                }
            },
            fields=("suggestedQuestions", "suggestedQuestionDetails"),
        )

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(
            result["differences"],
            ["doc1.suggestedQuestionDetails", "doc1.suggestedQuestions"],
        )

    def test_compare_requires_release_incompatible_field_to_be_deleted(self):
        result = compare_documents(
            [
                {
                    "questionId": "doc1",
                    "isChoiceOnly": False,
                    "explanationReferences": [
                        {
                            "title": "公式資料",
                            "sourceUrl": "https://example.test/reference",
                            "referenceDate": "2026-07-29",
                        }
                    ],
                }
            ],
            {
                "doc1": {
                    "isChoiceOnly": False,
                    "explanationReferences": [],
                }
            },
            fields=("isChoiceOnly", "explanationReferences"),
        )

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(
            result["differences"],
            ["doc1.explanationReferences"],
        )

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
        self.assertIn("correctChoiceText", database.field_paths)


if __name__ == "__main__":
    unittest.main()
