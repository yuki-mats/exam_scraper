import unittest

from scripts.check.audit_question_type_transitions import index_records, type_transitions


class QuestionTypeTransitionAuditTests(unittest.TestCase):
    def test_matches_by_identity_not_list_position(self):
        before = index_records([{"original_question_id": "a", "questionType": "true_false"}, {"original_question_id": "b", "questionType": "group_choice"}])
        after = index_records([{"original_question_id": "b", "questionType": "true_false"}, {"original_question_id": "a", "questionType": "group_choice"}])
        self.assertEqual(type_transitions(before, after), [{"recordId": "a", "beforeType": "true_false", "afterType": "group_choice"}])

    def test_initial_addition_is_not_evidence_of_overwrite(self):
        self.assertEqual(type_transitions({}, index_records({"a": "group_choice"})), [])

    def test_duplicate_identity_is_not_silently_overwritten(self):
        with self.assertRaises(ValueError):
            index_records([{"original_question_id": "a"}, {"original_question_id": "a"}])

    def test_wrapped_records_and_public_id_are_supported(self):
        self.assertEqual(index_records({"question_bodies": [{"public_question_id": "a", "questionType": "true_false"}]})["a"]["questionType"], "true_false")


if __name__ == "__main__":
    unittest.main()
