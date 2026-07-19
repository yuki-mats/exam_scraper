from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.scrape.udemy import (
    build_answer_result_text,
    build_source_record,
    load_browser_export,
    main as udemy_main,
    source_filename,
    source_identity,
    stable_question_url,
    validate_browser_export,
    validate_record,
)


def sample_record(*, quiz_id: str = "4699792", number: int = 1) -> dict:
    return {
        "course_slug": "aws-knan",
        "quiz_id": quiz_id,
        "quiz_title": "SAA-C03版模擬試験①",
        "question_number": number,
        "question_label": f"問題{number}",
        "question_text": "運用負荷を抑えて要件を満たす構成を選択してください。",
        "question_image_urls": [],
        "choices": [
            {"number": 1, "text": "選択肢A", "is_correct": True, "image_urls": []},
            {"number": 2, "text": "選択肢B", "is_correct": False, "image_urls": []},
        ],
        "correct_choice_numbers": [1],
        "selection_type": "radio",
        "explanation_text": "選択肢Aは要件を満たします。",
        "explanation_image_urls": [],
        "domain": "EC2",
        "reference_urls": [],
    }


class ScrapeUdemyTests(unittest.TestCase):
    def test_validate_browser_export_collects_immutable_quiz_question_keys(self) -> None:
        payload = {
            "schema_version": 1,
            "source_site": "tokyo-gas-dx-udemy-com",
            "course_slug": "aws-knan",
            "course_url": "https://tokyo-gas-dx.udemy.com/course/aws-knan/",
            "expected_count": 2,
            "quizzes": [
                {
                    "quiz_id": "4699792",
                    "expected_count": 2,
                    "records": [sample_record(number=1), sample_record(number=2)],
                }
            ],
        }

        self.assertEqual(len(validate_browser_export(payload)), 2)

    def test_load_browser_export_rejects_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.json"
            path.write_text(json.dumps({"schema_version": 2, "quizzes": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_browser_export(path)

    def test_build_source_record_uses_quiz_and_authored_number_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"QUESTION_ID_SECRET_KEY": "test-secret"}
        ):
            record = build_source_record(
                sample_record(),
                qualification_code="aws-solutions-architect-associate",
                qualification_name="AWS Certified Solutions Architect - Associate (SAA-C03)",
                output_list_group_id="udemy-aws-saa-c03",
                source_list_group_id="aws-knan",
                http_session=object(),
                image_output_dir=Path(directory),
            )

        self.assertEqual(
            record["source_question_id"],
            "aws-solutions-architect-associate:tokyo-gas-dx-udemy-com:course:aws-knan:quiz:4699792:question:001",
        )
        self.assertEqual(record["answer_result_inferred_correct_choice_numbers"], [1])
        self.assertNotIn("examYear", record)
        self.assertEqual(validate_record(record), [])

    def test_identity_filename_url_and_answer_text_are_stable(self) -> None:
        self.assertEqual(
            source_identity(
                qualification_code="aws-solutions-architect-associate",
                course_slug="aws-knan",
                quiz_id="4699792",
                question_number=7,
            ),
            "aws-solutions-architect-associate:tokyo-gas-dx-udemy-com:course:aws-knan:quiz:4699792:question:007",
        )
        self.assertEqual(source_filename("4699792", 7), "question_udemy-4699792-007.json")
        self.assertEqual(
            stable_question_url(course_slug="aws-knan", quiz_id="4699792", question_number=7),
            "https://tokyo-gas-dx.udemy.com/course/aws-knan/learn/quiz/4699792/test#question-007",
        )
        self.assertEqual(build_answer_result_text([3, 1]), "正解は 1、3 です。")

    def test_main_saves_then_verifies_all_exported_questions(self) -> None:
        payload = {
            "schema_version": 1,
            "source_site": "tokyo-gas-dx-udemy-com",
            "course_slug": "aws-knan",
            "course_title": "SAA-C03模擬試験",
            "course_url": "https://tokyo-gas-dx.udemy.com/course/aws-knan/",
            "expected_count": 2,
            "quizzes": [
                {
                    "quiz_id": "4699792",
                    "quiz_title": "SAA-C03版模擬試験①",
                    "expected_count": 2,
                    "records": [sample_record(number=1), sample_record(number=2)],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"QUESTION_ID_SECRET_KEY": "test-secret"}
        ):
            root = Path(directory)
            export_path = root / "browser-export.json"
            export_path.write_text(json.dumps(payload), encoding="utf-8")
            argv = [
                "--qualification-code",
                "aws-solutions-architect-associate",
                "--qualification-name",
                "AWS Certified Solutions Architect - Associate (SAA-C03)",
                "--list-url",
                "https://tokyo-gas-dx.udemy.com/course/aws-knan/",
                "--output-list-group-id",
                "udemy-aws-saa-c03",
                "--output-dir",
                str(root / "output"),
                "--browser-export",
                str(export_path),
            ]

            with redirect_stdout(StringIO()):
                self.assertEqual(udemy_main(argv), 0)
                self.assertEqual(udemy_main(argv), 0)

            source_dir = (
                root
                / "output"
                / "aws-solutions-architect-associate"
                / "questions_json"
                / "udemy-aws-saa-c03"
                / "00_source"
            )
            self.assertEqual(len(list(source_dir.glob("question_udemy-*.json"))), 2)
            report = json.loads(
                (
                    root
                    / "output"
                    / "aws-solutions-architect-associate"
                    / "reports"
                    / "udemy_aws-knan_scrape_result.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["newlySavedCount"], 0)
            self.assertEqual(report["updatedExistingCount"], 0)
            self.assertEqual(report["verifiedExistingCount"], 2)


if __name__ == "__main__":
    unittest.main()
