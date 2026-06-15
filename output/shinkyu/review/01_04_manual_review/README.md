# 鍼灸師 01-04 manual review prep

## Scope
- qualification: `shinkyu`
- questionsRoot: `output/shinkyu/questions_json`
- categoryPath: `output/shinkyu/category/category.json`
- source files: 244
- questions: 5560

## Workflow
- 01: `10_questionType_fixed/` の固定名ファイルを上書きする。
- 02: `15_correctChoiceText_fixed/` で `questionIntent` と `correctChoiceText` を上書きする。
- 03: `21_explanationText_added/` で `explanationText`、`suggestedQuestions`、`suggestedQuestionDetails` を上書きする。
- 04: `22_questionSetId_linked/` で `category.json` の `questionSets[].questionSetId` だけを付与する。
- 各問の `reviewDecision` は、一問ずつ確認が済むまで `pending` のままにする。

## Verification
```bash
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check /Users/yuki/development/exam_scraper/output/shinkyu/review/01_04_manual_review/shinkyu_01_04_manual_review.jsonl \
  --expected-total 5560 \
  --require-stage-files \
  --category output/shinkyu/category/category.json \
  --allow-pending
```

## Merge Per Year
```bash
for y in 1993 1994 1995 1996 1997 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026; do
  .venv/bin/python scripts/merge/00_merge_all.py "$y" --base-dir output/shinkyu/questions_json
done
```

## Year Counts
- 1993: 160
- 1994: 160
- 1995: 160
- 1996: 160
- 1997: 160
- 1998: 160
- 1999: 160
- 2000: 160
- 2001: 160
- 2002: 160
- 2003: 160
- 2004: 160
- 2005: 160
- 2006: 160
- 2007: 160
- 2008: 160
- 2009: 160
- 2010: 160
- 2011: 160
- 2012: 160
- 2013: 160
- 2014: 160
- 2015: 160
- 2016: 160
- 2017: 160
- 2018: 160
- 2019: 160
- 2020: 160
- 2021: 180
- 2022: 180
- 2023: 180
- 2024: 180
- 2025: 180
- 2026: 180
