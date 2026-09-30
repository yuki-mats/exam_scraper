import unittest

from scripts.pipeline.import_authored_original_questions import build_records


class ImportAuthoredOriginalQuestionsTest(unittest.TestCase):
    def setUp(self):
        self.category = {
            "metadata": {"qualificationId": "gx-kentei"},
            "questionSets": [{"questionSetId": "gx_qs01", "isDeleted": False}],
        }
        self.question = {
            "id": "q001",
            "questionBodyText": "GXの説明として適切なものはどれか。",
            "choiceTextList": ["経済と社会の変革を含む", "温室効果ガスの算定だけを指す"],
            "correctChoiceNumber": 1,
            "explanationText": "GXは脱炭素を通じた経済社会の変革を含む。",
            "questionSetId": "gx_qs01",
        }

    def test_stable_internal_identity_and_independent_publication_fields(self):
        payload = {"qualificationId": "gx-kentei", "listGroupId": "authored-gx-basic", "questions": [self.question]}
        _, _, outputs = build_records(payload, self.category)
        record = outputs[0][1]["question_bodies"][0]
        self.assertEqual(record["question_url"], "ankiplus://authored/gx-kentei/q001")
        self.assertEqual(record["examSource"], "独自問題")
        self.assertNotIn("examYear", record)
        self.assertEqual(record["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(record["_independentImageRequired"], False)
        self.assertEqual(record["public_question_id"], build_records(payload, self.category)[2][0][1]["question_bodies"][0]["public_question_id"])

    def test_rejects_invalid_answers_and_categories(self):
        for field, value in (("correctChoiceNumber", 3), ("questionSetId", "unknown")):
            with self.subTest(field=field):
                question = {**self.question, field: value}
                with self.assertRaises(ValueError):
                    build_records({"qualificationId": "gx-kentei", "listGroupId": "authored-gx-basic", "questions": [question]}, self.category)


if __name__ == "__main__":
    unittest.main()
