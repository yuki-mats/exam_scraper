# Source conflict practical review: gas-shunin-kou 2022 問14

- reviewedAt: `2026-07-10T01:10:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:313-317`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `最も不適切なものは選択肢4です。`

## Finding

- all five statements differ from the archive site only by separators, parentheses, or line-break formatting.
- The gas/material pairings are unchanged under the Firestore snapshot.
- Statements 1, 2, 3, and 5 remain `正しい`, and statement 4 remains `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-1006` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat separator, parenthesis, and line-break differences in material-pairing choices as formatting conflicts, not automatic source replacement.
- For single-choice `最も不適切なもの` questions, store the answer as a choice number rather than a count.
