# 2026-06-18 01-04 Manual Review Prep Board

## Scope

- qualification: `judoseifukushi`
- source root: `output/judoseifukushi/questions_json`
- active slice: `2001 / question_2001_3.json / 問51-問75`

## Completed

- `scripts/check/prepare_qualification_01_04_manual_review.py export` で全 7,600 問分の review JSONL、progress summary、README、年別メモを生成した。
- `scripts/check/prepare_qualification_01_04_manual_review.py check` で rowCount=7600、pending=7600、duplicatedReviewIds=0 を確認した。
- 次の file 単位レビューの作業台として `output/judoseifukushi/review/01_04_manual_review/` を使える状態にした。

## Artifacts

- `output/judoseifukushi/review/01_04_manual_review/judoseifukushi_01_04_manual_review.jsonl`
- `output/judoseifukushi/review/01_04_manual_review/judoseifukushi_01_04_progress_summary.json`
- `output/judoseifukushi/review/01_04_manual_review/README.md`
- `output/judoseifukushi/review/01_04_manual_review/years/judoseifukushi_01_04_manual_review_2001.md`

## Checks passed

- `python3 scripts/check/prepare_qualification_01_04_manual_review.py export judoseifukushi --expected-total 7600 --output-dir output/judoseifukushi/review/01_04_manual_review --write-year-markdown`
- `python3 scripts/check/prepare_qualification_01_04_manual_review.py check output/judoseifukushi/review/01_04_manual_review/judoseifukushi_01_04_manual_review.jsonl --expected-total 7600 --allow-pending`

## Notes

- stage skeletons は作っていないため、01-04 の実 patch は file 単位で引き続き作成する。
- 次の実作業は `output/judoseifukushi/questions_json/2001/00_source/question_2001_3.json` の `問51-問75`。
