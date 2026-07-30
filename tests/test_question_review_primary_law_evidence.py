from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.question_review_console.primary_law_evidence import (
    LawFileSnapshot,
    PrimaryLawEvidenceResolver,
    extract_locator_text,
    locator_parts,
)


def _law_xml(*, article_text: str, appendix_text: str = "別表本文") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Law>
  <LawBody>
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
