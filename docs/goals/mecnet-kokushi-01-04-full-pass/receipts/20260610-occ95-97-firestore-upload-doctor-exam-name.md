# 2026-06-10 occ95-97 Firestore upload with doctor exam name

## Scope

- Target qualification: `mecnet-kokushi`
- Target list groups: `95`, `96`, `97`
- Requested qualification/exam name: `医師`
- Intent: uploaded MEC Net kokushi questions to Firestore using `examSource` generated with `--exam-name 医師`.

## Dirty Tree Cleanup

- Pre-existing unrelated dirty files were moved out of the working tree with:
  - `git stash push -u -m "pre-firestore-upload-unrelated-dirty-20260610"`
- Stash reference after cleanup:
  - `stash@{0}: On codex/goal-driven-workflow: pre-firestore-upload-unrelated-dirty-20260610`
- Working tree was clean before upload work continued.

## Generated Firestore JSON

- `output/mecnet-kokushi/questions_json/upload_to_firestore/95_firestore_20260610_001856.json`
- `output/mecnet-kokushi/questions_json/upload_to_firestore/96_firestore_20260610_001904.json`
- `output/mecnet-kokushi/questions_json/upload_to_firestore/97_firestore_20260610_001910.json`

## Pre-upload Validation

Each list group was regenerated with:

```bash
.venv/bin/python scripts/pipeline/prepare_firestore_upload.py <list_group_id> \
  --base-dir output/mecnet-kokushi/questions_json \
  --exam-name 医師 \
  --questionset-only \
  --skip-update-category-counts \
  --upload-dry-run
```

Results:

- `95`
  - requirements check (merged): OK
  - requirements check (firestore): OK
  - questionSetId check: all IDs present in `category.json`
  - upload dry-run: OK
  - upload JSON question count: `1239`
  - upload-unable records: `0`
- `96`
  - requirements check (merged): OK
  - requirements check (firestore): OK
  - questionSetId check: all IDs present in `category.json`
  - upload dry-run: OK
  - upload JSON question count: `1685`
  - upload-unable records: `0`
- `97`
  - requirements check (merged): OK
  - requirements check (firestore): OK
  - questionSetId check: all IDs present in `category.json`
  - upload dry-run: OK
  - upload JSON question count: `1815`
  - upload-unable records: `0`

## Firestore Upload Result

Commands used:

```bash
.venv/bin/python scripts/upload/upload_questions_to_firestore.py \
  output/mecnet-kokushi/questions_json/upload_to_firestore/<list_group_id>_firestore_<timestamp>.json \
  --credentials-json /Users/yuki/.config/exam_scraper/repaso-rbaqy4-service-account.json
```

Results:

- `95`: updated `1239`, skipped `0`, errors `0`
- `96`: updated `1685`, skipped `0`, errors `0`
- `97`: updated `1815`, skipped `0`, errors `0`

## Live Firestore Verification

Queried `questions` where:

- `qualificationId == "mecnet-kokushi"`
- `listGroupId == "95"`, `"96"`, or `"97"`

Counts:

- `95`: live `1239`, JSON `1239`
- `96`: live `1685`, JSON `1685`
- `97`: live `1815`, JSON `1815`

Sample `examSource` values in live Firestore:

- `95`: `407705ba4d2ff862_w1` -> `医師, 2001年, 95A-6`
- `96`: `717a8c475007a5a4_1` -> `医師, 2002年, 96A-2, 設問1`
- `97`: `095e8d424ea4ad69_1` -> `医師, 2003年, 97A-1, 設問1`

## Category Note

- Category dry-run with `--licenseName 医師` passed schema validation.
- Actual category upload was not performed because this is still a partial occurrence upload.
- `category.json` currently has `questionCount=0` for all folders/questionSets, and applying partial counts would mark many category docs as zero-count/deleted before all list groups are ready.
