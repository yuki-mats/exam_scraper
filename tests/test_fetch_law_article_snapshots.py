from __future__ import annotations

import unittest

from scripts.pipeline.fetch_law_article_snapshots import (
    article_query_candidates,
    extract_verified_current_refs,
    normalize_article_for_api,
    parse_article_text,
)


class FetchLawArticleSnapshotsTests(unittest.TestCase):
    def test_normalizes_japanese_article_numbers_for_api(self) -> None:
        self.assertEqual(normalize_article_for_api("6条"), "6")
        self.assertEqual(normalize_article_for_api("第6条"), "6")
        self.assertEqual(normalize_article_for_api("22条の2"), "22_2")
        self.assertEqual(normalize_article_for_api("別表第2"), "別表第二")
        self.assertEqual(article_query_candidates("22条の2"), ["22_2", "22条の2"])
        self.assertEqual(article_query_candidates("別表第2"), ["別表第二", "別表第2"])

    def test_extracts_unique_verified_current_refs(self) -> None:
        refs = extract_verified_current_refs(
            [
                {
                    "questionId": "q1",
                    "originalQuestionId": "oq1",
                    "lawReferences": [
                        {
                            "role": "current_basis",
                            "verificationStatus": "verified",
                            "lawId": "325AC0000000201",
                            "lawTitle": "建築基準法",
                            "article": "6条",
                        },
                        {
                            "role": "exam_time_basis",
                            "verificationStatus": "verified",
                            "lawId": "325AC0000000201",
                            "lawTitle": "建築基準法",
                            "article": "6条",
                        },
                    ],
                },
                {
                    "questionId": "q2",
                    "originalQuestionId": "oq2",
                    "lawReferences": [
                        {
                            "role": "current_basis",
                            "verificationStatus": "verified",
                            "lawId": "325AC0000000201",
                            "lawTitle": "建築基準法",
                            "article": "6条",
                        }
                    ],
                },
            ]
        )

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["lawId"], "325AC0000000201")
        self.assertEqual(refs[0]["questionIds"], ["q1", "q2"])

    def test_parses_article_text_from_egov_xml(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot>
  <Result><Code>0</Code><Message/></Result>
  <ApplData>
    <LawContents>
      <Article Num="6">
        <ArticleCaption>（建築物の建築等に関する申請及び確認）</ArticleCaption>
        <ArticleTitle>第六条</ArticleTitle>
        <Paragraph Num="1">
          <ParagraphNum>１</ParagraphNum>
          <ParagraphSentence>
            <Sentence Num="1">建築主は、確認済証の交付を受けなければならない。</Sentence>
          </ParagraphSentence>
        </Paragraph>
      </Article>
    </LawContents>
  </ApplData>
</DataRoot>
"""

        text = parse_article_text(xml)

        self.assertIn("（建築物の建築等に関する申請及び確認）", text)
        self.assertIn("第六条", text)
        self.assertIn("１ 建築主は、確認済証の交付を受けなければならない。", text)

    def test_parses_appendix_table_text_from_egov_xml(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot>
  <Result><Code>0</Code><Message/></Result>
  <ApplData>
    <LawContents>
      <AppdxTable Num="2">
        <AppdxTableTitle>別表第二</AppdxTableTitle>
        <TableStruct>
          <Table><TableRow><TableColumn><Sentence>用途地域</Sentence></TableColumn></TableRow></Table>
        </TableStruct>
      </AppdxTable>
    </LawContents>
  </ApplData>
</DataRoot>
"""

        text = parse_article_text(xml)

        self.assertIn("別表第二", text)
        self.assertIn("用途地域", text)


if __name__ == "__main__":
    unittest.main()
