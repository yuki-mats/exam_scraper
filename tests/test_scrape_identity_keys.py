from __future__ import annotations

import os
import unittest

from scripts.scrape.common import (
    make_canonical_question_key,
    make_canonical_statement_keys,
    make_url_source_question_id,
    source_site_from_url,
)


class ScrapeIdentityKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QUESTION_ID_SECRET_KEY", "test-secret")

    def test_canonical_question_key_excludes_scrape_site(self) -> None:
        kakomonn_source = make_url_source_question_id(
            "building-env-health-manager",
            "https://birukan.kakomonn.com/questions/64445",
        )
        yakutik_source = make_url_source_question_id(
            "building-env-health-manager",
            "https://yaku-tik.com/bill/h30-001/",
        )

        self.assertNotEqual(kakomonn_source, yakutik_source)
        self.assertEqual(source_site_from_url("https://birukan.kakomonn.com/questions/1"), "kakomonn")
        self.assertEqual(source_site_from_url("https://yaku-tik.com/bill/h30-001/"), "yaku-tik")

        first = make_canonical_question_key(
            qualification_code="building-env-health-manager",
            exam_year=2018,
            question_label="問1 （建築物衛生行政概論 問1）",
        )
        second = make_canonical_question_key(
            qualification_code="building-env-health-manager",
            exam_occurrence_id="2018",
            question_number=1,
        )

        self.assertEqual(first, second)
        self.assertEqual(first, "building-env-health-manager:2018:q001")
        self.assertEqual(
            make_canonical_statement_keys(first, 3),
            [
                "building-env-health-manager:2018:q001:s01",
                "building-env-health-manager:2018:q001:s02",
                "building-env-health-manager:2018:q001:s03",
            ],
        )

    def test_section_code_disambiguates_repeated_local_question_numbers(self) -> None:
        first_subject = make_canonical_question_key(
            qualification_code="kougai",
            exam_year=2025,
            section_code="公害総論",
            question_number=1,
        )
        second_subject = make_canonical_question_key(
            qualification_code="kougai",
            exam_year=2025,
            section_code="大気概論",
            question_number=1,
        )

        self.assertNotEqual(first_subject, second_subject)
        self.assertTrue(first_subject.endswith(":q001"))
        self.assertTrue(second_subject.endswith(":q001"))


if __name__ == "__main__":
    unittest.main()
