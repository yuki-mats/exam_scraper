from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CODE_PATH = REPO_ROOT / "code.py"

spec = importlib.util.spec_from_file_location("exam_scraper_code_completeness", CODE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"failed to load module: {CODE_PATH}")
code_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(code_module)


def make_question(url: str, *, with_answer: bool = True):
    answer = None
    if with_answer:
        answer = code_module.AnswerResultData(
            answer_result_text="正解は1です。",
            answer_result_html="<p>正解は1です。</p>",
            selected_choice_numbers=[1],
            is_selected_choice_correct=True,
            inferred_correct_choice_numbers=[1],
        )
    return code_module.QuestionData(
        question_url=url,
        question_id=url.rsplit("/", 1)[-1],
        exam_label="令和7年度",
        question_label="問1",
        question_body_text="問題文",
        choice_text_list=["選択肢"],
        correct_choice_numbers=[1],
        answer_result_data=answer,
        explanations=[],
        question_image_filenames=[],
        choice_image_filenames_by_choice=[[]],
    )


class ScrapeCompletenessTests(unittest.TestCase):
    def test_accepts_complete_kakomonn_group(self) -> None:
        urls = [
            "https://birukan.kakomonn.com/questions/1",
            "https://birukan.kakomonn.com/questions/2",
        ]

        code_module.validate_complete_question_scrape(
            urls,
            [make_question(url) for url in urls],
        )

    def test_rejects_missing_question(self) -> None:
        urls = [
            "https://birukan.kakomonn.com/questions/1",
            "https://birukan.kakomonn.com/questions/2",
        ]

        with self.assertRaisesRegex(code_module.IncompleteScrapeError, "取得問題数"):
            code_module.validate_complete_question_scrape(urls, [make_question(urls[0])])

    def test_rejects_duplicate_target_url(self) -> None:
        url = "https://birukan.kakomonn.com/questions/1"

        with self.assertRaisesRegex(code_module.IncompleteScrapeError, "対象URLに重複"):
            code_module.validate_complete_question_scrape(
                [url, url],
                [make_question(url), make_question(url)],
            )

    def test_rejects_kakomonn_question_without_answer_evidence(self) -> None:
        url = "https://birukan.kakomonn.com/questions/1"

        with self.assertRaisesRegex(code_module.IncompleteScrapeError, "正答根拠"):
            code_module.validate_complete_question_scrape(
                [url],
                [make_question(url, with_answer=False)],
            )

    def test_allows_source_without_answer_result_endpoint(self) -> None:
        url = "https://example.test/questions/1"

        code_module.validate_complete_question_scrape(
            [url],
            [make_question(url, with_answer=False)],
        )


if __name__ == "__main__":
    unittest.main()
