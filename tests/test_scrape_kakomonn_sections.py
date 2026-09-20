from __future__ import annotations

import unittest

from code import extract_birukan_section_code
from scripts.scrape.common import make_canonical_question_key


class KakomonnSectionIdentityTests(unittest.TestCase):
    def test_extract_birukan_section_code_for_all_exam_subjects(self) -> None:
        occurrence = "第55回（令和7年度（2025年））"
        expected = {
            "建築物衛生行政概論": "eisei-gyosei",
            "建築物の環境衛生": "kankyo-eisei",
            "空気環境の調整": "kuki-kankyo",
            "建築物の構造概論": "kozo-gairon",
            "給水及び排水の管理": "kyusui-haisui",
            "清掃": "seiso",
            "ねずみ、昆虫等の防除": "nezumi-konchu",
        }

        for section_label, section_code in expected.items():
            with self.subTest(section_label=section_label):
                self.assertEqual(
                    extract_birukan_section_code(f"{occurrence} {section_label}"),
                    section_code,
                )

    def test_birukan_question_numbers_are_unique_across_subjects(self) -> None:
        occurrence = "第55回（令和7年度（2025年））"
        administration = extract_birukan_section_code(
            f"{occurrence} 建築物衛生行政概論"
        )
        environment = extract_birukan_section_code(
            f"{occurrence} 建築物の環境衛生"
        )

        first = make_canonical_question_key(
            qualification_code="birukan",
            exam_occurrence_id="2025-r55",
            exam_year=2025,
            question_label="問1",
            section_code=administration,
        )
        second = make_canonical_question_key(
            qualification_code="birukan",
            exam_occurrence_id="2025-r55",
            exam_year=2025,
            question_label="問1",
            section_code=environment,
        )

        self.assertEqual(first, "birukan:2025-r55:eisei-gyosei:q001")
        self.assertEqual(second, "birukan:2025-r55:kankyo-eisei:q001")
        self.assertNotEqual(first, second)

    def test_extract_birukan_section_code_rejects_unknown_labels(self) -> None:
        self.assertIsNone(extract_birukan_section_code("第55回（令和7年度（2025年））"))


if __name__ == "__main__":
    unittest.main()
