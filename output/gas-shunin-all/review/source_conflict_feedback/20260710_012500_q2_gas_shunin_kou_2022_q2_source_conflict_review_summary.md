# Source conflict practical review: gas-shunin-kou 2022 問2

- reviewedAt: `2026-07-10T01:25:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:349`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 1 つです。`

## Finding

- statement 1 differs in ESDS spelling/spacing, punctuation, and line-break formatting.
- Correctness under the Firestore snapshot is unchanged; statements 1 through 4 remain `間違い`, and statement 5 remains `正しい`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-40-164` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat English abbreviation spacing and punctuation differences as source-conflict review items, not automatic source replacement.
- Normalize `sourceExplanationChoiceCorrectness` to a per-choice array before removing P2 items from the remaining queue.
