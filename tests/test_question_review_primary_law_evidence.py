from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.question_review_console.primary_law_evidence import (
    LawFileSnapshot,
    PrimaryLawEvidenceError,
    PrimaryLawEvidenceResolver,
    extract_locator_text,
    locator_parts,
)


def _law_xml(*, article_text: str, appendix_text: str = "別表本文") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Law>
  <LawBody>
    <LawTitle>試験法</LawTitle>
    <MainProvision>
      <Article Num="11">
        <ArticleTitle>第十一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>{article_text}</Sentence></ParagraphSentence></Paragraph>
      </Article>
    </MainProvision>
    <AppdxTable Num="3">
      <AppdxTableTitle>別表第三</AppdxTableTitle>
      <RelatedArticle Num="11"/>
      <TableStruct><Table><TableRow><TableColumn>{appendix_text}</TableColumn></TableRow></Table></TableStruct>
    </AppdxTable>
  </LawBody>
</Law>
"""


class PrimaryLawEvidenceTests(unittest.TestCase):
    def test_wrong_law_title_is_not_accepted_as_primary_evidence(self):
        def fetcher(law_id, as_of):
            return LawFileSnapshot(
                law_id=law_id, as_of=as_of,
                source_url="https://example.test/law", revision_id="revision-1",
                xml_text=_law_xml(article_text="同じ条番号の別の法律の本文"),
            )

        with tempfile.TemporaryDirectory() as directory:
            resolver = PrimaryLawEvidenceResolver(Path(directory), fetcher=fetcher)
            for title, expected_status in (("別の法律", "partial"), ("試験法", "complete")):
                with self.subTest(title=title):
                    result = resolver.resolve({"lawReferences": [{
                        "lawId": "123AC0000000001", "lawTitle": title,
                        "article": "11", "role": "current_basis",
                    }]}, current_as_of="2026-09-21")
                    self.assertEqual(result["status"], expected_status)
                    item = result["items"][0]
                    self.assertEqual(item["resolvedLawTitle"], "試験法")
                    if expected_status == "partial":
                        self.assertIn("法令名が一致しません", item["error"])
                        self.assertNotIn("currentSnapshot", item)
                    else:
                        self.assertEqual(item["currentSnapshot"]["lawTitle"], "試験法")

    def test_xml_without_law_title_is_not_accepted(self):
        snapshot = LawFileSnapshot(
            law_id="123AC0000000001", as_of="2026-09-21",
            source_url="https://example.test/law", revision_id="revision-1",
            xml_text=_law_xml(article_text="本文").replace("<LawTitle>試験法</LawTitle>", ""),
        )
        with self.assertRaisesRegex(PrimaryLawEvidenceError, "正式法令名"):
            PrimaryLawEvidenceResolver._snapshot_payload(snapshot, kind="article", number=11)

    def test_repository_boiler_catalogs_cover_every_source_period(self):
        repo_root = Path(__file__).resolve().parents[1]
        resolver = PrimaryLawEvidenceResolver(repo_root)

        expected_boiler1 = {}
        for year in range(2009, 2026):
            expected_boiler1[f"{year}-1"] = f"{year}-06-30"
            expected_boiler1[f"{year}-2"] = f"{year}-12-31"
        actual_boiler1 = {
            list_group_id: value[0]
            for (qualification, list_group_id), value in resolver._exam_dates.items()
            if qualification == "boiler1"
        }
        self.assertEqual(actual_boiler1, expected_boiler1)

        expected_boiler2 = {}
        group_id = 60001
        for year in range(2015, 2026):
            expected_boiler2[str(group_id)] = f"{year - 1}-12-31"
            group_id += 1
            expected_boiler2[str(group_id)] = f"{year}-06-30"
            group_id += 1
        expected_boiler2["60023"] = "2025-12-31"
        actual_boiler2 = {
            list_group_id: value[0]
            for (qualification, list_group_id), value in resolver._exam_dates.items()
            if qualification == "boiler2"
        }
        self.assertEqual(actual_boiler2, expected_boiler2)

    def test_repository_birukan_catalog_covers_all_source_groups(self):
        repo_root = Path(__file__).resolve().parents[1]
        resolver = PrimaryLawEvidenceResolver(repo_root)

        expected = {
            "95001": "2018-10-07",
            "95002": "2019-10-06",
            "95003": "2020-10-04",
            "95004": "2021-10-03",
            "95005": "2022-10-02",
            "95006": "2023-10-01",
            "95007": "2024-10-06",
            "95008": "2025-10-05",
        }
        for list_group_id, exam_date in expected.items():
            self.assertEqual(
                resolver._exam_dates[("birukan", list_group_id)],
                (
                    exam_date,
                    "document/sources/birukan/official_exam_pdf_catalog.json",
                ),
            )

    def test_locator_parser_accepts_articles_and_appendix_tables(self):
        self.assertEqual(
            locator_parts("第11条、別表第三（二）"),
            (("article", 11), ("appendix_table", 3)),
        )
        self.assertEqual(locator_parts("145"), (("article", 145),))
        self.assertEqual(
            locator_parts("第4条の4の7"),
            (("article", (4, 4, 7)),),
        )
        self.assertEqual(
            locator_parts("64第1項"),
            (("article", 64),),
        )

    def test_xml_extraction_reads_full_locator_element(self):
        xml_text = _law_xml(article_text="条文本文", appendix_text="表のセル")
        self.assertIn(
            "条文本文",
            extract_locator_text(xml_text, "article", 11),
        )
        self.assertIn(
            "表のセル",
            extract_locator_text(xml_text, "appendix_table", 3),
        )

    def test_xml_extraction_reads_article_branch(self):
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<Law>
  <LawBody>
    <MainProvision>
      <Article Num="4"><ArticleTitle>第四条</ArticleTitle></Article>
      <Article Num="4_4_7">
        <ArticleTitle>第四条の四の七</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>枝番条文</Sentence></ParagraphSentence></Paragraph>
      </Article>
    </MainProvision>
  </LawBody>
</Law>
"""
        self.assertIn(
            "枝番条文",
            extract_locator_text(xml_text, "article", (4, 4, 7)),
        )
        self.assertNotIn(
            "枝番条文",
            extract_locator_text(xml_text, "article", 4),
        )

    def test_exam_and_current_snapshots_are_compared(self):
        calls: list[tuple[str, str]] = []

        def fetcher(law_id: str, as_of: str) -> LawFileSnapshot:
            calls.append((law_id, as_of))
            return LawFileSnapshot(
                law_id=law_id,
                as_of=as_of,
                source_url=f"https://example.test/{law_id}?asof={as_of}",
                revision_id=f"{law_id}_{as_of}",
                xml_text=_law_xml(article_text="同一条文"),
            )

        with tempfile.TemporaryDirectory() as directory:
            resolver = PrimaryLawEvidenceResolver(
                Path(directory),
                fetcher=fetcher,
            )
            result = resolver.resolve(
                {
                    "lawReferences": [
                        [
                            {
                                "role": "exam_time_basis",
                                "choiceIndex": 1,
                                "lawId": "346M50000400027",
                                "article": "第11条、別表第三",
                                "referenceDate": "2022-11-15",
                            }
                        ]
                    ]
                },
                current_as_of="2026-07-30",
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(
            {item["comparison"] for item in result["items"]},
            {"unchanged"},
        )
        self.assertEqual(
            sorted(set(calls)),
            [
                ("346M50000400027", "2022-11-15"),
                ("346M50000400027", "2026-07-30"),
            ],
        )

    def test_current_reference_uses_official_exam_date_catalog(self):
        calls: list[tuple[str, str]] = []

        def fetcher(law_id: str, as_of: str) -> LawFileSnapshot:
            calls.append((law_id, as_of))
            return LawFileSnapshot(
                law_id=law_id,
                as_of=as_of,
                source_url=f"https://example.test/{law_id}?asof={as_of}",
                revision_id=f"{law_id}_{as_of}",
                xml_text=_law_xml(article_text="同一条文"),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = (
                root
                / "document"
                / "sources"
                / "sample"
                / "official_exam_pdf_catalog.json"
            )
            catalog.parent.mkdir(parents=True)
            catalog.write_text(
                """{
  "qualificationIds": ["sample"],
  "examDates": {"2024": "2024-09-29"}
}
""",
                encoding="utf-8",
            )
            resolver = PrimaryLawEvidenceResolver(
                root,
                fetcher=fetcher,
            )
            result = resolver.resolve(
                {
                    "lawReferences": [
                        {
                            "role": "current_basis",
                            "choiceIndex": 1,
                            "lawId": "346M50000400027",
                            "article": "第11条",
                            "referenceDate": "2026-07-30",
                        }
                    ]
                },
                current_as_of="2026-07-30",
                qualification="sample",
                list_group_id="2024",
            )

        self.assertEqual(result["examAsOf"], "2024-09-29")
        self.assertEqual(
            result["examAsOfSource"],
            "document/sources/sample/official_exam_pdf_catalog.json",
        )
        self.assertEqual(result["items"][0]["comparison"], "unchanged")
        self.assertEqual(
            result["items"][0]["examAsOfSource"],
            "document/sources/sample/official_exam_pdf_catalog.json",
        )
        self.assertEqual(
            sorted(set(calls)),
            [
                ("346M50000400027", "2024-09-29"),
                ("346M50000400027", "2026-07-30"),
            ],
        )

    def test_shared_cache_fetches_one_law_revision_once_under_concurrency(self):
        call_count = 0
        call_lock = threading.Lock()

        def fetcher(law_id: str, as_of: str) -> LawFileSnapshot:
            nonlocal call_count
            with call_lock:
                call_count += 1
            return LawFileSnapshot(
                law_id=law_id,
                as_of=as_of,
                source_url="https://example.test/law",
                revision_id="revision-1",
                xml_text=_law_xml(article_text="条文"),
            )

        with tempfile.TemporaryDirectory() as directory:
            resolver = PrimaryLawEvidenceResolver(
                Path(directory),
                fetcher=fetcher,
            )
            with ThreadPoolExecutor(max_workers=8) as executor:
                snapshots = list(
                    executor.map(
                        lambda _index: resolver.law_file(
                            "346M50000400027",
                            "2026-07-30",
                        ),
                        range(16),
                    )
                )

        self.assertEqual(call_count, 1)
        self.assertEqual(
            {snapshot.revision_id for snapshot in snapshots},
            {"revision-1"},
        )

    def test_concurrent_fetch_failure_is_shared_for_the_same_revision(self):
        call_count = 0
        call_lock = threading.Lock()

        def fetcher(law_id: str, as_of: str) -> LawFileSnapshot:
            nonlocal call_count
            with call_lock:
                call_count += 1
            time.sleep(0.02)
            raise PrimaryLawEvidenceError(
                f"official endpoint unavailable: {law_id} / {as_of}"
            )

        with tempfile.TemporaryDirectory() as directory:
            resolver = PrimaryLawEvidenceResolver(
                Path(directory),
                fetcher=fetcher,
                failure_cache_ttl_seconds=60,
            )
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(
                        resolver.law_file,
                        "346M50000400027",
                        "2026-07-30",
                    )
                    for _index in range(16)
                ]
                for future in futures:
                    with self.assertRaisesRegex(
                        PrimaryLawEvidenceError,
                        "official endpoint unavailable",
                    ):
                        future.result()

        self.assertEqual(call_count, 1)

    def test_official_fetch_concurrency_is_bounded_across_revisions(self):
        active_count = 0
        peak_count = 0
        count_lock = threading.Lock()

        def fetcher(law_id: str, as_of: str) -> LawFileSnapshot:
            nonlocal active_count, peak_count
            with count_lock:
                active_count += 1
                peak_count = max(peak_count, active_count)
            try:
                time.sleep(0.03)
                return LawFileSnapshot(
                    law_id=law_id,
                    as_of=as_of,
                    source_url="https://example.test/law",
                    revision_id=f"{law_id}-{as_of}",
                    xml_text=_law_xml(article_text="条文"),
                )
            finally:
                with count_lock:
                    active_count -= 1

        with tempfile.TemporaryDirectory() as directory:
            resolver = PrimaryLawEvidenceResolver(
                Path(directory),
                fetcher=fetcher,
                max_parallel_fetches=3,
            )
            with ThreadPoolExecutor(max_workers=12) as executor:
                snapshots = list(
                    executor.map(
                        lambda index: resolver.law_file(
                            f"law-{index}",
                            "2026-07-30",
                        ),
                        range(12),
                    )
                )

        self.assertEqual(len(snapshots), 12)
        self.assertGreaterEqual(peak_count, 2)
        self.assertLessEqual(peak_count, 3)
