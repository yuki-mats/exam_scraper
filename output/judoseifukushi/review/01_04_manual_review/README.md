# 柔道整復師 01-04 manual review prep

## Scope
- qualification: `judoseifukushi`
- questionsRoot: `output/judoseifukushi/questions_json`
- categoryPath: `output/judoseifukushi/category/category.json`
- source files: 316
- questions: 7600

## Workflow
- 正本: `prompt/README.md` と各工程promptに従う。
- 02は`15_correctChoiceText_fixed/`、02aは`23_correctChoiceText_fixed/`へ分ける。
- 保存先・ファイル名は`document/operations/artifact_contract.md`を参照する。
- 各問の `reviewDecision` は、一問ずつ確認が済むまで `pending` のままにする。

## Verification
```bash
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check /Users/yuki/development/exam_scraper_work/output/judoseifukushi/review/01_04_manual_review/judoseifukushi_01_04_manual_review.jsonl \
  --expected-total 7600 \
  --require-stage-files \
  --category output/judoseifukushi/category/category.json \
  --allow-pending
```

## Merge Per Year
```bash
for y in 1993 1994 1995 1996 1997 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026; do
  .venv/bin/python scripts/merge/00_merge_all.py "$y" --base-dir output/judoseifukushi/questions_json
done
```

## Year Counts
- 1993: 200
- 1994: 200
- 1995: 200
- 1996: 200
- 1997: 200
- 1998: 200
- 1999: 200
- 2000: 200
- 2001: 200
- 2002: 200
- 2003: 200
- 2004: 200
- 2005: 230
- 2006: 230
- 2007: 230
- 2008: 230
- 2009: 230
- 2010: 230
- 2011: 230
- 2012: 230
- 2013: 230
- 2014: 230
- 2015: 230
- 2016: 230
- 2017: 230
- 2018: 230
- 2019: 230
- 2020: 250
- 2021: 250
- 2022: 250
- 2023: 250
- 2024: 250
- 2025: 250
- 2026: 250
