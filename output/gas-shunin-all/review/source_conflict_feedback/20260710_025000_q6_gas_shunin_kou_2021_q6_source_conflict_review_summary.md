# Source conflict practical review: gas-shunin-kou 2021 問6

- reviewedAt: `2026-07-10T02:50:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:259-260`
- verdict: `preserve_firestore_snapshot_after_primary_law_review`
- answer: `正解は 2 です。`

## Finding

- statement 2 remains `間違い`: Gas Business Act Article 101(3) starts the 30-day period from acceptance of the notification, not submission.
- statement 4 remains `正しい`: Enforcement Regulation Article 153(1) and Appended Table 1 make the described gas-generator remodeling a construction-plan notification target.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-10-060` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Route construction-plan notification-target questions to Article 153 and Appended Table 1, not only pressure definitions.
- For 30-day construction-start restrictions, route to Gas Business Act Article 101(3).
