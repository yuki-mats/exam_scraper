# Source conflict practical review: gas-shunin-kou 2022 問27

- reviewedAt: `2026-07-10T00:25:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:380`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 5 つです。`

## Finding

- statement 1 has only a wording difference: `接続部分` vs `接続部`.
- Meaning and correctness are unchanged; all five statements remain `正しい`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-60-199` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat small wording differences in connector/gas tap topics as source-conflict review items, not answer changes, unless the technical meaning changes.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
