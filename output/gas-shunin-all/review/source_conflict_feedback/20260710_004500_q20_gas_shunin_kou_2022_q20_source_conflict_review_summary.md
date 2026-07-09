# Source conflict practical review: gas-shunin-kou 2022 問20

- reviewedAt: `2026-07-10T00:45:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:370-371`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 3 つです。`

## Finding

- statement 1 has only unit notation and line-break differences: `㎥` vs `m³N`.
- statement 4 has only tilde and line-break differences: `～` vs `〜`.
- Meaning and correctness under the Firestore snapshot are unchanged; statements 2, 3, and 5 remain `正しい`, and statements 1 and 4 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-60-182` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat unit-symbol and tilde differences in combustion topics as source-conflict review items, not automatic source replacement.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
