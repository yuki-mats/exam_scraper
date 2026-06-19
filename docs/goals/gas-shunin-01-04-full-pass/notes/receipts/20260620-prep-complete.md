# T001 prep complete

result: done

## Outputs

- `output/gas-shunin-kou/category/category.json`
- `output/gas-shunin-otsu/category/category.json`
- `output/gas-shunin-kou/review/01_04_manual_review/gas-shunin-kou_01_04_manual_review.jsonl`
- `output/gas-shunin-otsu/review/01_04_manual_review/gas-shunin-otsu_01_04_manual_review.jsonl`
- `output/gas-shunin-kou/questions_json/*/{10_questionType_fixed,15_correctChoiceText_fixed,21_explanationText_added,22_questionSetId_linked}/*.json`
- `output/gas-shunin-otsu/questions_json/*/{10_questionType_fixed,15_correctChoiceText_fixed,21_explanationText_added,22_questionSetId_linked}/*.json`

## Counts

- gas-shunin-kou: 412 questions, 22 source files
- gas-shunin-otsu: 522 questions, 27 source files
- total: 934 questions

## Verification

```bash
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check output/gas-shunin-kou/review/01_04_manual_review/gas-shunin-kou_01_04_manual_review.jsonl --expected-total 412 --require-stage-files --category output/gas-shunin-kou/category/category.json --allow-pending
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check output/gas-shunin-otsu/review/01_04_manual_review/gas-shunin-otsu_01_04_manual_review.jsonl --expected-total 522 --require-stage-files --category output/gas-shunin-otsu/category/category.json --allow-pending
.venv/bin/python -m unittest tests.test_prepare_qualification_01_04_manual_review tests.test_materialize_gas_shunin_site_source tests.test_materialize_gas_shunin_firestore_source tests.test_build_gas_shunin_source_key_mapping tests.test_scrape_presets
```

All passed.

## Notes

- Firestore由来の甲種重複 `originalQuestionId` は `firestore:<doc ids>` の `reviewQuestionId` で分離した。
- patch雛形では `original_question_id` をレビューキーとして使い、元のFirestore `originalQuestionId` は `source_original_question_id` に保持する。
- 実レビューは未開始。全行は pending のまま。
