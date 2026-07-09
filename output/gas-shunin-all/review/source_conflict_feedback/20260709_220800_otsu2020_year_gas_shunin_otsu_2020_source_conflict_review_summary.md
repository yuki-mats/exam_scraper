# Source conflict practical review: gas-shunin-otsu 2020

- reviewedAt: `2026-07-09T22:08:00+09:00`
- reviewRecords: `4`
- machineReadable: `output/gas-shunin-all/review/source_conflict_feedback/20260709_220800_otsu2020_year_gas_shunin_otsu_2020_source_conflict_review.jsonl`
- boundary: `00_source` remains unchanged.

## Conflict type counts

- `answer_result_text_conflict`: `1`
- `choice_count_and_missing_choice_conflict`: `2`
- `existing_firestore_source_text_conflict`: `1`

## Finding

- Wrong or incomplete source-site answer/choice data is not copied back into `00_source`.
- Prepared patches preserve corrected answer/explanation behavior and keep source conflict decisions machine-readable.
