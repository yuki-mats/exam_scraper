# Source conflict practical review: gas-shunin-kou 2022 問25

- reviewedAt: `2026-07-10T00:35:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:376-377`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 2 つです。`

## Finding

- statement 3 has only a wording/format difference: `形状のみで決まる` vs `形状のみにより決まる`.
- statement 5 has only notation/wording differences around `%`, `～/〜`, and `死亡に至る/死に至る`.
- Meaning and correctness are unchanged; statements 1 and 2 remain `正しい`, and statements 3, 4, and 5 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-60-177` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat small wording and symbol differences in ventilation/CO poisoning topics as source-conflict review items, not answer changes, unless the technical meaning changes.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
