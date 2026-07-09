# Source conflict practical review: gas-shunin-kou 2022 問16

- reviewedAt: `2026-07-10T01:05:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:320-321`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `誤っているものは 4 つです。`

## Finding

- statement 3 differs as `溶接事業所の溶接士ごと` vs `溶接事業所かつ溶接士ごと`, plus line-break formatting.
- statement 4 differs only as `開先不良とは` vs `開先不良は`.
- Meaning and correctness under the Firestore snapshot are unchanged; statements 1, 2, 3, and 5 remain `間違い`, and statement 4 remains `正しい`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-80-192` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat close technical-term wording differences in welding and inspection topics as source-conflict review items, not automatic source replacement.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
