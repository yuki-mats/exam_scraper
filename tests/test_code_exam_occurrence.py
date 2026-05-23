from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CODE_PATH = REPO_ROOT / "code.py"

spec = importlib.util.spec_from_file_location("exam_scraper_code", CODE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"failed to load module: {CODE_PATH}")
code_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(code_module)


class ExamOccurrenceIdTests(unittest.TestCase):
    def test_build_exam_occurrence_id_patterns(self) -> None:
        cases = [
            ("令和7年（2025年） 学科1（建築計画）", "2025"),
            ("令和7年（2025年）10月 午後", "2025-10-pm"),
            ("第8回（2025年） 午前", "2025-r8-am"),
            ("第1回 追加試験（2018年） 午後", "2018-r1-extra-pm"),
            ("第36回（令和5年度） 介護過程", "2023-r36"),
            ("第８回（２０２５年） 午前", "2025-r8-am"),
        ]

        for exam_label, expected in cases:
            with self.subTest(exam_label=exam_label):
                self.assertEqual(code_module.build_exam_occurrence_id(exam_label), expected)

    def test_extract_exam_year_value_supports_japanese_era_only(self) -> None:
        self.assertEqual(
            code_module.extract_exam_year_value("第32回（令和元年度） 人間関係とコミュニケーション"),
            2019,
        )


if __name__ == "__main__":
    unittest.main()
