# Source conflict practical review: gas-shunin-kou 2022 問19

- reviewedAt: `2026-07-10T00:50:00+09:00`
- conflictLedger: `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl:363-369`
- verdict: `preserve_firestore_snapshot_no_answer_impact`
- answer: `正しいものは 5 つです。`

## Finding

- The site source adds a WI/MCP compatibility diagram description to the question body; the existing Firestore body, image state, and statement-level IDs are preserved.
- statement 2 has only wording differences around the relation between combustion gas temperature and air ratio.
- statement 4 has only parenthesis notation and `燃え去る/燃える` wording differences.
- Meaning and correctness under the Firestore snapshot are unchanged; all five statements remain `正しい`.
- Existing Firestore statement-level IDs and `questionSetId=chiefgasengineerlicense-A-60-182` are preserved.
- `00_source` remains unchanged.

## Feedback For Search Improvement

- Treat figure-description deltas in compatibility-diagram questions as source-conflict review items, not automatic source replacement.
- For P2 non-Lawzilla items, require `answer_result_text` and per-choice `sourceExplanationChoiceCorrectness` before removing from the remaining queue.
