# 2026-06-20 official questionSetId gate

## Summary

公害防止管理者の13資格区分別 `category.json` は生成済みだが、現行 `output/kougai/questions_json/*/22_questionSetId_linked/` は旧 yaku-tik topic 由来の coarse `questionSetId` を使っている。

そのため、資格区分別 upload JSON へ materialize する前に、04 作業で `output/kougai/category/category.json` の公式 119 questionSets へ再割当する必要がある。

## Gate command

```bash
.venv/bin/python scripts/check/check_kougai_official_question_set_ids.py --json
```

## Current result

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2160,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 240,
    "kougai_qs02_baifun": 240,
    "kougai_qs03_daitai": 160,
    "kougai_qs04_daisui": 160,
    "kougai_qs05_osui": 400,
    "kougai_qs06_suigai": 160,
    "kougai_qs07_suiyuu": 240,
    "kougai_qs08_taigai": 160,
    "kougai_qs09_taitoku": 240,
    "kougai_qs10_taiyuu": 160
  }
}
```

## Progress

### 2026-06-20: 2025 `question_2025_yakutik_1`

- Added reviewed batch: `notes/qset-batches/2025-yakutik-1-official-qsets.json`
- Applied to local generated file: `output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_1_questionSetId_linked.json`
- Scope: 25 records
  - 公害総論: 15 records
  - 水質概論: 10 records
- Target file check:

```bash
.venv/bin/python scripts/check/check_questionSetId.py \
  --category output/kougai/category/category.json \
  --fixed output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_1_questionSetId_linked.json \
  --questionset-only
```

Result: 25 records, 8 unique official questionSetIds, all present in `category.json`.

Updated gate result:

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2135,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 225,
    "kougai_qs02_baifun": 240,
    "kougai_qs03_daitai": 160,
    "kougai_qs04_daisui": 160,
    "kougai_qs05_osui": 400,
    "kougai_qs06_suigai": 150,
    "kougai_qs07_suiyuu": 240,
    "kougai_qs08_taigai": 160,
    "kougai_qs09_taitoku": 240,
    "kougai_qs10_taiyuu": 160
  }
}
```

### 2026-06-20: 2025 `question_2025_yakutik_2`

- Added reviewed batch: `notes/qset-batches/2025-yakutik-2-official-qsets.json`
- Applied to local generated file: `output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_2_questionSetId_linked.json`
- Scope: 25 汚水処理特論 records
- Target file check: 25 records, 5 unique official questionSetIds, all present in `category.json`.

Updated gate result:

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2110,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 225,
    "kougai_qs02_baifun": 240,
    "kougai_qs03_daitai": 160,
    "kougai_qs04_daisui": 160,
    "kougai_qs05_osui": 375,
    "kougai_qs06_suigai": 150,
    "kougai_qs07_suiyuu": 240,
    "kougai_qs08_taigai": 160,
    "kougai_qs09_taitoku": 240,
    "kougai_qs10_taiyuu": 160
  }
}
```

### 2026-06-20: 2025 `question_2025_yakutik_3`

- Added reviewed batch: `notes/qset-batches/2025-yakutik-3-official-qsets.json`
- Applied to local generated file: `output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_3_questionSetId_linked.json`
- Scope: 25 records
  - 水質有害物質特論: 15 records
  - 大規模水質特論: 10 records
- Target file check: 25 records, 5 unique official questionSetIds, all present in `category.json`.

Updated gate result:

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2085,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 225,
    "kougai_qs02_baifun": 240,
    "kougai_qs03_daitai": 160,
    "kougai_qs04_daisui": 150,
    "kougai_qs05_osui": 375,
    "kougai_qs06_suigai": 150,
    "kougai_qs07_suiyuu": 225,
    "kougai_qs08_taigai": 160,
    "kougai_qs09_taitoku": 240,
    "kougai_qs10_taiyuu": 160
  }
}
```

### 2026-06-20: 2025 `question_2025_yakutik_4`

- Added reviewed batch: `notes/qset-batches/2025-yakutik-4-official-qsets.json`
- Applied to local generated file: `output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_4_questionSetId_linked.json`
- Scope: 25 records
  - 大気概論: 10 records
  - 大気特論: 15 records
- Target file check: 25 records, 10 unique official questionSetIds, all present in `category.json`.

Updated gate result:

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2060,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 225,
    "kougai_qs02_baifun": 240,
    "kougai_qs03_daitai": 160,
    "kougai_qs04_daisui": 150,
    "kougai_qs05_osui": 375,
    "kougai_qs06_suigai": 150,
    "kougai_qs07_suiyuu": 225,
    "kougai_qs08_taigai": 150,
    "kougai_qs09_taitoku": 225,
    "kougai_qs10_taiyuu": 160
  }
}
```

### 2026-06-20: 2025 `question_2025_yakutik_5`

- Added reviewed batch: `notes/qset-batches/2025-yakutik-5-official-qsets.json`
- Applied to local generated file: `output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_5_questionSetId_linked.json`
- Scope: 25 records
  - ばいじん・粉じん特論: 15 records
  - 大気有害物質特論: 10 records
- Target file check: 25 records, 8 unique official questionSetIds, all present in `category.json`.

Updated gate result:

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2035,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 225,
    "kougai_qs02_baifun": 225,
    "kougai_qs03_daitai": 160,
    "kougai_qs04_daisui": 150,
    "kougai_qs05_osui": 375,
    "kougai_qs06_suigai": 150,
    "kougai_qs07_suiyuu": 225,
    "kougai_qs08_taigai": 150,
    "kougai_qs09_taitoku": 225,
    "kougai_qs10_taiyuu": 150
  }
}
```

### 2026-06-20: 2025 `question_2025_yakutik_6`

- Added reviewed batch: `notes/qset-batches/2025-yakutik-6-official-qsets.json`
- Applied to local generated file: `output/kougai/questions_json/2025/22_questionSetId_linked/question_2025_yakutik_6_questionSetId_linked.json`
- Scope: 10 大規模大気特論 records
- Target file check: 10 records, 5 unique official questionSetIds, all present in `category.json`.

Updated gate result:

```json
{
  "filesScanned": 96,
  "recordsScanned": 2160,
  "invalidRecordCount": 2025,
  "invalidQuestionSetIdCounts": {
    "kougai_qs01_kousou": 225,
    "kougai_qs02_baifun": 225,
    "kougai_qs03_daitai": 150,
    "kougai_qs04_daisui": 150,
    "kougai_qs05_osui": 375,
    "kougai_qs06_suigai": 150,
    "kougai_qs07_suiyuu": 225,
    "kougai_qs08_taigai": 150,
    "kougai_qs09_taitoku": 225,
    "kougai_qs10_taiyuu": 150
  }
}
```

### 2025 completion checkpoint

- Completed reviewed official questionSetId batches for all 2025 yaku-tik 22-stage files.
- 2025 local generated files updated:
  - `question_2025_yakutik_1_questionSetId_linked.json`: 25 records
  - `question_2025_yakutik_2_questionSetId_linked.json`: 25 records
  - `question_2025_yakutik_3_questionSetId_linked.json`: 25 records
  - `question_2025_yakutik_4_questionSetId_linked.json`: 25 records
  - `question_2025_yakutik_5_questionSetId_linked.json`: 25 records
  - `question_2025_yakutik_6_questionSetId_linked.json`: 10 records
- Total fixed for 2025: 135 records.
- Gate delta: `invalidRecordCount` 2160 -> 2025.

## Next rule

- 旧IDを機械的に公式IDへ置換しない。
- 旧IDは folder 相当の粗い分類であり、公式PDFの numbered range までは確定できない。
- 04 は問題本文・選択肢・解説を見て、`kougai_qsNN_MM` の公式 questionSetId を一問ずつ付与する。
- この gate が `invalidRecordCount: 0` になってから、`scripts/pipeline/materialize_kougai_qualification_uploads.py` で13資格区分へ展開する。
