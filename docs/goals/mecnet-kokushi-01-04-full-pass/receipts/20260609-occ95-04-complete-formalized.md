# 2026-06-09 occ95 04 complete formalized

## Scope

- qualification: `mecnet-kokushi`
- occurrence: `95`
- step: `04_prompt_add_questionSetId.md`
- intent: `22_questionSetId_linked/wip` の raw mapping を source 順で完結させ、checker が読める formal patch に昇格する

## Local artifacts

- `output/mecnet-kokushi/questions_json/95/22_questionSetId_linked/wip/raw_questionSetId_batch033_20260609_2240.json`
- `output/mecnet-kokushi/questions_json/95/22_questionSetId_linked/question_95_questionSetId_linked_20260609_2241.json`

## Final covered source questions

- 257 `e3379d9cda68296b` -> `mk_bp_general_05_08`
- 258 `29621e3dde0a3de5` -> `mk_bp_general_05_05`
- 259 `35c59d7b1c5d5156` -> `mk_bp_general_05_05`

## Verification

```bash
python3 scripts/fix/materialize_minimal_patch.py \
  --task question_set \
  --source output/mecnet-kokushi/questions_json/95/20_merged_1/question_95_merged.json \
  --raw "$TMP_RAW" \
  --output output/mecnet-kokushi/questions_json/95/22_questionSetId_linked/question_95_questionSetId_linked_20260609_2241.json
```

```bash
python3 scripts/check/check_question_set_patch_coverage.py \
  --source output/mecnet-kokushi/questions_json/95/20_merged_1/question_95_merged.json \
  --patch output/mecnet-kokushi/questions_json/95/22_questionSetId_linked/question_95_questionSetId_linked_20260609_2241.json \
  --category output/mecnet-kokushi/category/category.json \
  --questionset-only
```

```bash
python3 scripts/merge/00_merge_all.py 95 -d output/mecnet-kokushi/questions_json
```

```bash
python3 scripts/check/report_mecnet_kokushi_full_pass_progress.py
```

## Results

- `raw_questionSetId_batch033_20260609_2240.json valid ids: 3`
- `occ95 raw first259 order ok: 259`
- `materialized 259 entries`
- `questionSet coverage: OK`
- `merge_all completed for occ95`
- progress report after formalization: `01=7`, `02=7`, `03=6`, `04=1`, `20=7`

## Notes

- `95回` の prompt04 はこれで formal patch まで到達した。
- `30_merged_2` は explanation と `questionSetId` を反映して再生成された。
- 次は `96回` の prompt04 raw mapping へ進める。
