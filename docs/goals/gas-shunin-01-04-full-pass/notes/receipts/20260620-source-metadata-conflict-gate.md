# Source Metadata Repair And Conflict Gate

## Scope

- Target: gas-shunin `00_source`, question plan, and upload safety gate.
- Firestore document IDs were not changed.
- `originalQuestionBodyText` and `originalQuestionChoiceText` were not rewritten.
- Output source files under `output/gas-shunin-*/questions_json/*/00_source/` remain local generated artifacts.

## Changes

- Added metadata-only repair script:
  - `scripts/pipeline/repair_gas_shunin_source_metadata_conflicts.py`
- Normalized subject metadata from `hourei` to canonical `law`.
- Fixed the mixed 2023 kou law q02 Firestore/site source-status metadata by preserving Firestore natural keys and assigning site shadow keys.
- Updated the `00_source` contract checker to accept canonical `law` and resolved source-key conflicts.
- Exported Firestore/site content conflicts to:
  - `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl`
  - `output/gas-shunin-kou/review/source_conflicts/summary.json`
- Applied conflict flags to the all-question plan:
  - `sourceConflictStatus`
  - `sourceContentConflictCount`
  - `sourceContentConflictFields`
  - `sourceContentConflictLedgerPath`
- Added upload gate:
  - `scripts/check/check_gas_shunin_upload_gate.py`

## Validation

- `check_gas_shunin_00_source_contract.py`
  - sourceFileCount: 49
  - questionCount: 934
  - duplicateSourceUniqueKeyCount: 0
  - duplicateReviewQuestionIdCount: 0
  - issueCount: 0
- `check_gas_shunin_source_consistency.py`
  - metadata/status issues: 0
  - stored source unique key duplicates: 0
  - Firestore/site content conflicts remain: 441 fields across 121 canonical questions
- `check_gas_shunin_upload_gate.py`
  - default mode: fails with 125 unresolved source-conflict plan rows
  - `--allow-source-conflicts`: passes when no upload JSON is supplied
- Python compile check passed for the new and edited gas-shunin scripts.

## Current Policy

- Firestore is the priority source for already uploaded questions.
- Site/PDF/screenshot sources may be used only as review inputs when Firestore is missing or when a conflict row is explicitly reviewed.
- `sourceQuestionKey` and `sourceUniqueKeys` are merge/review keys only. They must not replace existing Firestore document IDs.
- Existing Firestore-derived upload rows must keep `questionId` within the known `firestoreQuestionIds` for the same `originalQuestionId`.
- Rows with `sourceConflictStatus = needs_source_review` must be visually reviewed before 01-04 output is treated as upload-ready.
