# あん摩マッサージ指圧師 01-04 manual review prep

## Scope
- qualification: `anma`
- questionsRoot: `output/anma/questions_json`
- categoryPath: `output/anma/category/category.json`
- source files: 210
- questions: 5159

## Workflow
- 正本: `prompt/README.md` と各工程promptに従う。
- 02は`15_correctChoiceText_fixed/`、02aは`23_correctChoiceText_fixed/`へ分ける。
- 保存先・ファイル名は`document/operations/artifact_contract.md`を参照する。
- 各問の `reviewDecision` は、一問ずつ確認が済むまで `pending` のままにする。

## Verification
```bash
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check /Users/yuki/development/exam_scraper_work/output/anma/review/01_04_manual_review/anma_01_04_manual_review.jsonl \
  --expected-total 5159 \
  --require-stage-files \
  --category output/anma/category/category.json \
  --allow-pending
```

## Merge Per Year
```bash
for y in 1993 1994 1995 1996 1997 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026; do
  .venv/bin/python scripts/merge/00_merge_all.py "$y" --base-dir output/anma/questions_json
done
```

## Year Counts
- 1993: 150
- 1994: 150
- 1995: 150
- 1996: 150
- 1997: 150
- 1998: 150
- 1999: 150
- 2000: 150
- 2001: 150
- 2002: 150
- 2003: 150
- 2004: 150
- 2005: 150
- 2006: 150
- 2007: 150
- 2008: 150
- 2009: 150
- 2010: 150
- 2011: 150
- 2012: 150
- 2013: 150
- 2014: 150
- 2015: 150
- 2016: 150
- 2017: 150
- 2018: 150
- 2019: 150
- 2020: 150
- 2021: 160
- 2022: 160
- 2023: 160
- 2024: 159
- 2025: 160
- 2026: 160
