from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.common.image_storage_urls import build_public_storage_url
from scripts.convert import convert_merged_to_firestore as module
from scripts.convert.convert_merged_to_firestore import archive_existing_entries


class ConvertMergedToFirestoreTests(unittest.TestCase):
    def test_archive_existing_entries_avoids_timestamp_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir)
            (target_dir / "old" / "20260408_223000").mkdir(parents=True)
            sample = target_dir / "sample.json"
            sample.write_text("{}", encoding="utf-8")

            fake_now = mock.Mock()
            fake_now.strftime.return_value = "20260408_223000"

            with mock.patch("scripts.convert.convert_merged_to_firestore.datetime") as mocked_datetime:
                mocked_datetime.now.return_value = fake_now
                archive_dir = archive_existing_entries(target_dir)

            self.assertEqual(archive_dir, target_dir / "old" / "20260408_223000_01")
            self.assertTrue((archive_dir / "sample.json").exists())

    def test_main_normalizes_legacy_image_urls_in_convert_and_upload_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            base_dir = root / "output" / "sample-qualification" / "questions_json"
            merged_dir = base_dir / "85010" / "30_merged_2"
            merged_dir.mkdir(parents=True, exist_ok=True)
            legacy_question_url = (
                "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
                "question_images%2Fofficial%2Fsample-qualification%2F85010%2Fqsample_q_img01.png?alt=media"
            )
            legacy_choice_url = (
                "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
                "question_images%2Fofficial%2Fsample-qualification%2F2024%2Fqsample_ch01_img01.png?alt=media"
            )
            merged_path = merged_dir / "question_85010_1_merged.json"
            merged_path.write_text(
                json.dumps(
                    {
                        "list_group_id": "85010",
                        "question_bodies": [
                            {
                                "questionType": "single_choice",
                                "original_question_id": "qsample",
                                "original_question_body_text": "元問題文",
                                "questionBodyText": "問題文",
                                "choiceTextList": ["選択肢A", "選択肢B"],
                                "correctChoiceText": ["選択肢A"],
                                "explanationText": ["解説"],
                                "questionImageStorageUrls": [legacy_question_url],
                                "originalQuestionChoiceImageUrls": [[legacy_choice_url], []],
                                "examYear": 2024,
                                "questionLabel": "問1",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            exit_code = module.main(["85010", "--base-dir", str(base_dir)])

            self.assertEqual(exit_code, 0)
            convert_output = max((base_dir / "85010" / "40_convert").glob("*.json"))
            upload_output = max((base_dir / "upload_to_firestore").glob("*.json"))
            expected_question_url = build_public_storage_url(
                "sample-qualification",
                "qsample_q_img01.png",
            )
            expected_choice_url = build_public_storage_url(
                "sample-qualification",
                "qsample_ch01_img01.png",
            )

            convert_data = json.loads(convert_output.read_text(encoding="utf-8"))
            upload_data = json.loads(upload_output.read_text(encoding="utf-8"))
            self.assertEqual(
                convert_data["questions"][0]["questionImageUrls"],
                [expected_question_url],
            )
            self.assertEqual(
                convert_data["questions"][0]["originalQuestionChoiceImageUrls"],
                [expected_choice_url],
            )
            self.assertEqual(
                upload_data["questions"][0]["questionImageUrls"],
                [expected_question_url],
            )
            self.assertEqual(
                upload_data["questions"][0]["originalQuestionChoiceImageUrls"],
                [expected_choice_url],
            )


if __name__ == "__main__":
    unittest.main()
