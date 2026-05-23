from __future__ import annotations

import unittest

from scripts.convert.convert_merged_to_firestore import get_original_question_body_text


class ConvertMergedToFirestoreTests(unittest.TestCase):
    def test_get_original_question_body_text_falls_back_to_question_body_text(self) -> None:
        question_body = {
            "questionBodyText": "  元の問題文として使う本文  ",
            "originalQuestionBodyText": "",
            "original_question_body_text": None,
        }

        self.assertEqual(
            get_original_question_body_text(question_body),
            "元の問題文として使う本文",
        )


if __name__ == "__main__":
    unittest.main()
