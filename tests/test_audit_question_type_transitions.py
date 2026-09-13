import unittest
import json
import tempfile
from pathlib import Path

from scripts.check.audit_question_type_transitions import audit, git, index_records, type_transitions


class QuestionTypeTransitionAuditTests(unittest.TestCase):
    def test_audit_reads_a_fixed_revision_without_changing_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "--initial-branch=main")
            path = repo / "output/sample/questions_json/2026/10_questionType_fixed/q.json"
            path.parent.mkdir(parents=True)
            for value in ("true_false", "group_choice", "true_false"):
                path.write_text(json.dumps([{"original_question_id": "q1", "questionType": value}], indent=2), encoding="utf-8")
                git(repo, "add", ".")
                git(repo, "-c", "user.name=Audit Test", "-c", "user.email=audit@example.invalid", "commit", "-m", value)
            before = git(repo, "status", "--porcelain")
            result = audit(repo, "HEAD~1")
            self.assertEqual(result["summary"]["recordCount"], 1)
            self.assertEqual(result["summary"]["readErrorCount"], 0)
            self.assertEqual(result["records"][0]["currentPatchType"], "group_choice")
            self.assertEqual(audit(repo, "HEAD")["records"][0]["currentPatchType"], "true_false")
            self.assertEqual(git(repo, "status", "--porcelain"), before)

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
