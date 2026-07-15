import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.work_versions import QuestionWorkVersionStore


def question(*, law_related=False):
    return {
        "id": "question-1",
        "reviewKey": "sample:2026:question_1:original-1",
        "qualification": "sample",
        "publicationQualificationId": "sample-public",
        "listGroupId": "2026",
        "originalQuestionId": "original-1",
        "isLawRelated": law_related,
    }


def policy(stage_id="question_type", *, fingerprint="fingerprint-1"):
    return {
        "id": stage_id,
        "code": "01" if stage_id == "question_type" else "03b",
        "label": "問題形式" if stage_id == "question_type" else "現行法監査",
        "policyVersion": 1,
        "policyFingerprint": fingerprint,
    }


class QuestionWorkVersionStoreTests(unittest.TestCase):
    def test_store_rejects_parent_path_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            with self.assertRaisesRegex(ValueError, "invalid"):
                store.path_for("..", "2026")

    def test_legacy_version_is_outdated_and_current_run_replaces_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            current_policy = policy()

            initial = store.status_for(item, [current_policy])
            legacy = store.record_stage(
                [item],
                current_policy,
                run_id=None,
                source="firestore_published_backfill",
                version=0,
                policy_fingerprint_override="legacy-unknown",
            )
            old = store.status_for(item, [current_policy])
            current = store.record_stage(
                [item],
                current_policy,
                run_id="run-1",
                source="validated_run",
            )
            complete = store.status_for(item, [current_policy])
            record = store.record_for(item)

        self.assertEqual(initial["status"], "unrecorded")
        self.assertEqual(legacy["recordedCount"], 1)
        self.assertEqual(old["status"], "outdated")
        self.assertEqual(old["stages"][0]["recordedVersion"], 0)
        self.assertEqual(current["recordedCount"], 1)
        self.assertTrue(complete["allCurrent"])
        self.assertEqual(complete["stages"][0]["runId"], "run-1")
        self.assertEqual(
            [entry["version"] for entry in record["stages"]["question_type"]["history"]],
            [0],
        )

    def test_backfill_never_overwrites_a_validated_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            current_policy = policy()
            store.record_stage(
                [item], current_policy, run_id="run-1", source="validated_run"
            )

            receipt = store.record_stage(
                [item],
                current_policy,
                run_id=None,
                source="firestore_published_backfill",
                only_missing=True,
                version=0,
                policy_fingerprint_override="legacy-unknown",
            )
            status = store.status_for(item, [current_policy])

        self.assertEqual(receipt["recordedCount"], 0)
        self.assertEqual(receipt["skippedCount"], 1)
        self.assertTrue(status["allCurrent"])
        self.assertEqual(status["stages"][0]["runId"], "run-1")

    def test_same_version_with_changed_policy_fingerprint_stays_current(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            store.record_stage(
                [item], policy(), run_id="run-1", source="validated_run"
            )

            status = store.status_for(
                item, [policy(fingerprint="fingerprint-changed")]
            )

        self.assertEqual(status["status"], "current")
        self.assertFalse(status["stages"][0]["policyFingerprintMatches"])

    def test_law_audit_version_applies_only_to_law_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            non_law = question(law_related=False)
            receipt = store.record_stage(
                [non_law],
                policy("law_audit"),
                run_id="run-1",
                source="validated_run",
            )
            status = store.status_for(non_law, [policy("law_audit")])

        self.assertEqual(receipt["recordedCount"], 0)
        self.assertEqual(status["applicableCount"], 0)

    def test_corrupt_group_file_fails_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            path = store.path_for("sample", "2026")
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "読めません"):
                store.record_for(item)
            with self.assertRaisesRegex(ValueError, "読めません"):
                store.record_stage(
                    [item], policy(), run_id="run-1", source="validated_run"
                )
            unchanged = path.read_text(encoding="utf-8")

        self.assertEqual(unchanged, "{broken")


if __name__ == "__main__":
    unittest.main()
