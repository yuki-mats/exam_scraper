# Source conflict practical review: gas-shunin-kou 2022 問1

- reviewedAt: `2026-07-10T01:30:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:348`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `誤っているものは 5 つです。`

## Finding

- statement 1 differs only in punctuation and line-break formatting.
- Correctness under the Firestore snapshot is unchanged; statements 1 through 5 remain `間違い`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-40-173` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat punctuation and line-break differences as source-conflict review items, not automatic source replacement.
- Keep non-Lawzilla technical items in the same yearly remaining queue until answer text and per-choice correctness are explicit.
- Record the practical answer impact separately so later search improvements can distinguish content conflicts from formatting drift.
