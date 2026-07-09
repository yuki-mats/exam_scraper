# Source conflict practical review: gas-shunin-kou 2022 問26

- reviewedAt: `2026-07-10T00:30:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:378-379`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 1 つです。`

## Finding

- statement 4 has only a notation difference: `サーミスタ` vs `サーミスター`.
- statement 5 has only wording/format differences: `残留未燃焼ガス/点火動作を行う装置` vs `残留未燃ガス/点火動作する装置`.
- Meaning and correctness are unchanged; statement 4 remains `正しい`, and statements 1, 2, 3, and 5 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-60-178` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat small wording differences in gas appliance safety-device topics as source-conflict review items, not answer changes, unless the technical meaning changes.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
