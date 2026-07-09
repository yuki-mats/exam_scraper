# Source conflict practical review: gas-shunin-kou 2022 問3

- reviewedAt: `2026-07-10T01:20:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:350-356`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `誤っているものは 3 つです。`

## Finding

- question body differs as `電気・計装設備` vs `電気、計装設備`; the prompt meaning is unchanged.
- statement 3 differs only by the separator in `貯槽・配管等`; statement 5 differs by punctuation and line-breaks.
- Correctness under the Firestore snapshot is unchanged; statements 1, 2, and 5 remain `間違い`, and statements 3 and 4 remain `正しい`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-40-185` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat body punctuation differences and choice separator differences as reviewable formatting conflicts, not automatic source replacement.
- Normalize `sourceExplanationChoiceCorrectness` to a per-choice array before removing P2 items from the remaining queue.
