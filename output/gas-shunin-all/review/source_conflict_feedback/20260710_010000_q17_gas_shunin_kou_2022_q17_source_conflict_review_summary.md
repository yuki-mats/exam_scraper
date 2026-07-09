# Source conflict practical review: gas-shunin-kou 2022 問17

- reviewedAt: `2026-07-10T01:00:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:322`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `誤っているものは 5 つです。`

## Finding

- statement 3 differs as `支管供管一括採水装置` vs `支管供給管一括採水装置`, plus line-break formatting.
- The existing Firestore statement remains incorrect because it says the later drainage step uses a pig instead of the air-increase ejector flow approach reflected in the reviewed explanation.
- Meaning and correctness under the Firestore snapshot are unchanged; statements 1 through 5 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-80-197` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat close technical-term differences in gas-main maintenance topics as source-conflict review items, not automatic source replacement.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
