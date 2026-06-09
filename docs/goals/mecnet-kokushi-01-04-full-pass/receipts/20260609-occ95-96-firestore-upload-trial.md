# 2026-06-09 occ95-96 Firestore upload trial

## Scope
- Target qualification: `mecnet-kokushi`
- Target list groups: `95`, `96`
- Reason: first trial upload at a clean occurrence boundary.
- Excluded: `97` and later, because `97` prompt04 is still raw WIP and not formalized.
- Category count update: skipped. `2_update_category_counts.py --latest-upload-only` currently expects latest upload JSON for all list groups, so partial occurrence upload should not update category counts.

## Generated Firestore JSON
- `output/mecnet-kokushi/questions_json/upload_to_firestore/95_firestore_20260609_224525.json`
- `output/mecnet-kokushi/questions_json/upload_to_firestore/96_firestore_20260609_224542.json`

## Pre-upload validation
### occ95
- `answer_result_text` missing: 0
- requirements check (merged): OK
- requirements check (firestore): OK
- questionSetId check: all IDs present in `category.json`
- upload dry-run: OK
- upload JSON question count: 1239
- upload-unable records: 0

### occ96
- `answer_result_text` missing: 0
- requirements check (merged): OK
- requirements check (firestore): OK
- questionSetId check: all IDs present in `category.json`
- upload dry-run: OK
- upload JSON question count: 1685
- upload-unable records: 0

## Firestore upload result
### occ95
- command: `.venv/bin/python scripts/upload/upload_questions_to_firestore.py output/mecnet-kokushi/questions_json/upload_to_firestore/95_firestore_20260609_224525.json`
- total: 1239
- updated: 1239
- skipped: 0
- errors: 0

### occ96
- command: `.venv/bin/python scripts/upload/upload_questions_to_firestore.py output/mecnet-kokushi/questions_json/upload_to_firestore/96_firestore_20260609_224542.json`
- total: 1685
- updated: 1685
- skipped: 0
- errors: 0

## Live Firestore verification
Queried `questions` where:
- `qualificationId == "mecnet-kokushi"`
- `listGroupId == "95"` or `"96"`

Results:
- `95`: 1239 docs
- `96`: 1685 docs

These counts match the generated upload JSON counts.

## Notes
- Existing unrelated dirty tree was not reverted or cleaned.
- Receipt is the tracked record for this trial upload.
- Local generated outputs remain under `output/mecnet-kokushi/questions_json`.
