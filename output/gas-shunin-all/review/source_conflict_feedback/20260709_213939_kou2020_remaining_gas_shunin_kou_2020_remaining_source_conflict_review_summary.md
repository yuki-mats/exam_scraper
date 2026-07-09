# Source conflict practical review: gas-shunin-kou 2020 remaining block

- reviewedAt: `2026-07-09T21:39:39+09:00`
- targetRecords: `30`
- machineReadable: `output/gas-shunin-all/review/source_conflict_feedback/20260709_213939_kou2020_remaining_gas_shunin_kou_2020_remaining_source_conflict_review.jsonl`
- verdict: preserve Firestore snapshot / existing statement-level IDs after review.
- `00_source` remains unchanged.

## Finding

- 30 source-conflict workflow records were reviewed against the conflict ledger and existing explanation patches.
- Archive-site wording/correctness conflicts are not used to rewrite Firestore-derived question text or IDs.
- Per-choice `sourceExplanationChoiceCorrectness` and explanation text remain the answer source for the prepared patch.
