# Source conflict practical review: gas-shunin-kou 2022 問8

- reviewedAt: `2026-07-10T01:15:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:361-362`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `誤っているものは 3 つです。`

## Finding

- statement 3 differs in the particle/wording around grain boundary corrosion.
- statement 4 differs in wording and line-breaks around welded portions and heat-affected zones.
- Meaning and correctness under the Firestore snapshot are unchanged; statements 1, 4, and 5 remain `間違い`, and statements 2 and 3 remain `正しい`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-40-165` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat technical wording differences in corrosion and welded-portion descriptions as source-conflict review items, not automatic source replacement.
- Normalize `sourceExplanationChoiceCorrectness` to a per-choice array before removing P2 items from the remaining queue.
