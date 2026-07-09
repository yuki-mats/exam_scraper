# Source conflict practical review: gas-shunin-kou 2022 問18

- reviewedAt: `2026-07-10T00:55:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:323`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 3 つです。`

## Finding

- statement 4 differs as `設計対策` vs `設備対策`, plus line-break formatting.
- The existing Firestore statement remains incorrect because it assigns the gas-supply restart principle to emergency measures rather than recovery measures.
- Meaning and correctness under the Firestore snapshot are unchanged; statements 2, 3, and 5 remain `正しい`, and statements 1 and 4 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-80-194` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat close technical-term differences in earthquake countermeasure topics as source-conflict review items, not automatic source replacement.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
