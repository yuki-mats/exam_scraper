# 2026-06-10 occ95-97 category count update and Firestore upload

## Scope

- Target qualification: `mecnet-kokushi`
- Target list groups: `95`, `96`, `97`
- Requested license name for folders: `医師`
- Intent: update `output/mecnet-kokushi/category/category.json` question counts from the current Firestore upload JSON, then register `folders` and `questionSets` in Firestore.

## Category Count Update

Command used:

```bash
.venv/bin/python scripts/count_questions/2_update_category_counts.py \
  output/mecnet-kokushi/category/category.json \
  output/mecnet-kokushi/questions_json/upload_to_firestore \
  --write
```

Result:

- scanned files: `3`
- distinct `questionSetId` values found: `72`
- changed `questionSets`: `72`
- changed `folders`: `9`
- backup: `output/mecnet-kokushi/category/old/category.json.bak_20260610_003710`

Local category totals after update:

- folders: `23`
- questionSets: `200`
- folder `questionCount` sum: `4163`
- questionSet `questionCount` sum: `4163`
- nonzero folders: `9`
- nonzero questionSets: `72`

Note: category counts are produced by `scripts.common.question_counting.analyze_question_file` for user-facing question records. This is different from expanded Firestore `questions` documents, where true/false and similar sub-records expand to `4739` documents across `95`, `96`, and `97`.

## Category Dry Run

Command used:

```bash
.venv/bin/python scripts/upload/upload_category_to_firestore.py \
  output/mecnet-kokushi/category/category.json \
  --licenseName 医師
```

Result:

- schema validation passed for all folders and questionSets
- no Firestore writes were made in dry-run mode

## Firestore Upload

Command used:

```bash
.venv/bin/python scripts/upload/upload_category_to_firestore.py \
  output/mecnet-kokushi/category/category.json \
  --licenseName 医師 \
  --upload \
  --credentials-json /Users/yuki/.config/exam_scraper/repaso-rbaqy4-service-account.json
```

Result:

- uploaded/skipped folders: `23`
- uploaded/skipped questionSets: `200`
- command completed with `完了`
- folder `licenseName`: `医師`
- folder/questionSet `qualificationId`: `mecnet-kokushi`

## Live Firestore Verification

Queried Firestore after upload:

- `folders` where `qualificationId == "mecnet-kokushi"`: `23`
- `questionSets` where `qualificationId == "mecnet-kokushi"`: `200`
- `questions` where `qualificationId == "mecnet-kokushi"`: `4739`
- `questionSets` where `qualificationId == "mecnet-kokushi"` and `isDeleted == false`: `200`

Sample live folder documents:

- `mk_bp_general_01`: `licenseName=医師`, `questionCount=186`, `qualificationId=mecnet-kokushi`
- `mk_bp_general_05`: `licenseName=医師`, `questionCount=804`, `qualificationId=mecnet-kokushi`
- `mk_bp_general_09`: `licenseName=医師`, `questionCount=653`, `qualificationId=mecnet-kokushi`

Sample live questionSet documents:

- `mk_bp_general_08_01`: `isDeleted=false`, `questionCount=279`, `qualificationId=mecnet-kokushi`
- `mk_bp_general_09_02`: `isDeleted=false`, `questionCount=207`, `qualificationId=mecnet-kokushi`
- `mk_bp_specific_01_01`: `isDeleted=false`, `questionCount=0`, `qualificationId=mecnet-kokushi`

`category.json` explicitly keeps zero-count questionSets as `isDeleted=false`, and `upload_category_to_firestore.py` respects that explicit field.
