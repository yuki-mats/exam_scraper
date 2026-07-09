# Source conflict practical review: gas-shunin-kou 2022 問22

- reviewedAt: `2026-07-10T00:40:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:372-373`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 3 つです。`

## Finding

- statement 3 differs as `水頭差` vs `水頭圧`, but the existing Firestore statement remains correct in context.
- statement 4 differs as `シスコンターダー` vs `シスターン` and `設計されている` vs `設けられている`; the existing Firestore statement remains incorrect and is preserved.
- Meaning and correctness under the Firestore snapshot are unchanged; statements 2, 3, and 5 remain `正しい`, and statements 1 and 4 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-60-169` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat technical term conflicts in household gas water-heater topics as source-conflict review items, not automatic source replacement.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
