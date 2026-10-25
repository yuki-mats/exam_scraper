import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.image_storage_urls import build_public_storage_url
from scripts.common.independent_question_images import (
    originalized_candidate_base,
    validate_independent_upload_image_gate,
)
from scripts.common.question_identity import SourceIdentityBinding
from scripts.merge.patch_views import validate_originalized_entry
from tools.question_review_console.question_candidate import (
    CandidateTarget, CandidateUpdate, QuestionCandidate, validate_candidate_content,
)
from tools.question_review_console.question_patch_proposal import IsolatedQuestionPatchWorkspace


class OriginalizeTextFirstTest(unittest.TestCase):
    def setUp(self):
        self.source_url = build_public_storage_url("sample", "source.png")
        self.generated_url = build_public_storage_url("sample", "originalized_q1_diagram_1.png")
        self.source = {
            "questionBodyText": "図に示す関係はどれか。",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["間違い", "正しい"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は2です。",
            "questionImageSourceUrls": ["https://example.invalid/source.png"],
            "questionImageStorageUrls": [self.source_url],
        }
        self.target = CandidateTarget(
            "q1:originalized", "originalized", "05_originalized/q1.json",
            ("questionBodyText", "questionImageStorageUrls"),
        )
        self.text_update = {"questionBodyText": "図の関係として適切なものを選びなさい。"}

    def candidate(self, fields):
        return QuestionCandidate("q1", "candidate", "本文を確定した。", (
            CandidateUpdate(self.target.target_id, fields, ()),
        ))

    def errors(self, projected, fields):
        return validate_candidate_content(
            self.candidate(fields), (self.target,), projected,
            original_source_record=self.source,
        )

    def test_source_figure_allows_text_first_but_upload_still_requires_image(self):
        self.assertEqual(self.errors(self.source, self.text_update), ())
        entry = originalized_candidate_base(self.source) | self.text_update
        self.assertTrue(validate_originalized_entry(self.source, entry))
        self.assertIn("questionImageStorageUrls", self.source)
        with self.assertRaisesRegex(ValueError, "独自生成画像がありません"):
            validate_independent_upload_image_gate([entry | {
                "isOfficial": True, "examSource": "独自問題",
                "questionId": "q1", "_independentImageRequired": True,
            }], "sample")

    def test_explicit_source_image_reuse_is_rejected(self):
        errors = self.errors(self.source, self.text_update | {
            "questionImageStorageUrls": [self.source_url],
        })
        self.assertTrue(any("取得元画像と同じURL" in error for error in errors))

    def test_refresh_preserves_existing_independent_image(self):
        current = originalized_candidate_base(self.source) | {
            "_independentImageRequired": True,
            "questionImageStorageUrls": [self.generated_url],
        }
        self.assertEqual(self.errors(current, self.text_update), ())
        self.assertEqual(originalized_candidate_base(current)["questionImageStorageUrls"],
                         [self.generated_url])
        # The marker is provenance, not permission to accept a source image.
        current["questionImageStorageUrls"] = [self.source_url]
        self.assertTrue(any("取得元画像と同じURL" in e for e in self.errors(current, self.text_update)))

    def test_new_patch_saved_without_source_image_and_existing_asset_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = "output/sample/questions_json/group/05_originalized/q1.json"
            (root / "output/sample").mkdir(parents=True)
            binding = SourceIdentityBinding.from_mapping({
                "reviewQuestionId": "q1", "sourceQuestionKey": "sample:group:q1",
                "sourceRecordRef": "q1.json#0",
            })
            workspace = IsolatedQuestionPatchWorkspace.create(
                root, root / "output/workspace", qualification="sample", mutable_paths=[path],
            )
            workspace.apply_record_update(path, binding=binding, aliases={"q1"},
                set_fields=self.text_update, base_record=originalized_candidate_base(self.source))
            saved = json.loads((workspace.root / path).read_text())[0]
            self.assertNotIn("questionImageStorageUrls", saved)
            self.assertNotIn("questionImageSourceUrls", saved)
            workspace.apply_record_update(path, binding=binding, aliases={"q1"},
                set_fields={"questionImageStorageUrls": [self.generated_url]}, base_record={})
            workspace.apply_record_update(path, binding=binding, aliases={"q1"},
                set_fields=self.text_update, base_record=originalized_candidate_base(self.source))
            saved = json.loads((workspace.root / path).read_text())[0]
            self.assertEqual(saved["questionImageStorageUrls"], [self.generated_url])


if __name__ == "__main__":
    unittest.main()
