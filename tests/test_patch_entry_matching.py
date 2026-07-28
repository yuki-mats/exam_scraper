import unittest

from scripts.check.patch_entry_matching import match_patch_entries


def record(question_no: int, *, patch: bool = False) -> dict:
    review_id = f"review-{question_no}"
    value = {
        "sourceQuestionKey": f"sample:2026:q{question_no:02d}",
        "reviewQuestionId": review_id,
        "sourceRecordRef": f"question.json#{question_no - 1}",
        "question_url": f"https://example.com/q{question_no}",
    }
    value["original_question_id"] = (
        f"firestore:document-{question_no}" if patch else review_id
    )
    return value


class PatchEntryMatchingTests(unittest.TestCase):
    def test_exact_bindings_allow_completion_order_and_enriched_original_id(self):
        sources = [record(1), record(2)]
        patches = [record(2, patch=True), record(1, patch=True)]

        matches, errors, warnings = match_patch_entries(sources, patches)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(
            [match.source["reviewQuestionId"] for match in matches],
            ["review-2", "review-1"],
        )

    def test_complete_but_inconsistent_binding_is_rejected(self):
        sources = [record(1)]
        patch = record(1, patch=True)
        patch["reviewQuestionId"] = "different-review-id"

        matches, errors, _ = match_patch_entries(sources, [patch])

        self.assertEqual(matches, [])
        self.assertTrue(any("exact source identity mismatch" in error for error in errors))

    def test_legacy_entry_uses_unique_alias(self):
        sources = [record(1)]
        patch = {
            "original_question_id": "review-1",
            "question_url": "https://example.com/q1",
        }

        matches, errors, _ = match_patch_entries(sources, [patch])

        self.assertEqual(errors, [])
        self.assertEqual(matches[0].source_index, 0)

    def test_duplicate_source_claim_is_rejected(self):
        sources = [record(1)]
        patches = [record(1, patch=True), record(1, patch=True)]

        _, errors, _ = match_patch_entries(sources, patches)

        self.assertTrue(any("duplicate source record" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
