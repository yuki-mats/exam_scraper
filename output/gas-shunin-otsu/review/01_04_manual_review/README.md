# ガス主任技術者乙種 01-04 manual review prep

## Scope
- qualification: `gas-shunin-otsu`
- questionsRoot: `output/gas-shunin-otsu/questions_json`
- categoryPath: `output/gas-shunin-otsu/category/category.json`
- source files: 27
- questions: 522

## Workflow
- 01: `10_questionType_fixed/` の固定名ファイルを上書きする。
- 02: `15_correctChoiceText_fixed/` で `questionIntent` と `correctChoiceText` を上書きする。
- 03: `21_explanationText_added/` で `explanationText`、`suggestedQuestions`、`suggestedQuestionDetails` を上書きする。
- 04: `22_questionSetId_linked/` で `category.json` の `questionSets[].questionSetId` だけを付与する。
- 各問の `reviewDecision` は、一問ずつ確認が済むまで `pending` のままにする。

## Verification
```bash
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check /Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ/400_アプリ開発・運営/exam_scraper_drive_candidate/output/gas-shunin-otsu/review/01_04_manual_review/gas-shunin-otsu_01_04_manual_review.jsonl \
  --expected-total 522 \
  --require-stage-files \
  --category output/gas-shunin-otsu/category/category.json \
  --allow-pending
```

## Merge Per Year
```bash
for y in 2017 2018 2019 2020 2021 2022 2023 2024 2025; do
  .venv/bin/python scripts/merge/00_merge_all.py "$y" --base-dir output/gas-shunin-otsu/questions_json
done
```

## Year Counts
- 2017: 58
- 2018: 58
- 2019: 58
- 2020: 58
- 2021: 58
- 2022: 58
- 2023: 58
- 2024: 58
- 2025: 58
