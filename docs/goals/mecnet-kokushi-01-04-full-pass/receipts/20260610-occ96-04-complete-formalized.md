# 2026-06-10 occ96 04 complete formalized

## Scope

- qualification: `mecnet-kokushi`
- occurrence: `96`
- step: `04_prompt_add_questionSetId.md`
- intent: `22_questionSetId_linked/wip` の raw mapping を source 順で完結させ、checker が読める formal patch に昇格する

## Local artifacts

- `output/mecnet-kokushi/questions_json/96/22_questionSetId_linked/wip/raw_questionSetId_batch043_20260610_0030.json`
- `output/mecnet-kokushi/questions_json/96/22_questionSetId_linked/question_96_questionSetId_linked_20260609_2204.json`

## Final covered source questions

- 321 `c817ed79608c16ef` -> `mk_bp_general_04_01`
- 322 `8c07bdd878972208` -> `mk_bp_general_03_10`
- 323 `aa868c3dd0071eb2` -> `mk_bp_general_08_03`
- 324 `d6e94bf62dc2cbe9` -> `mk_bp_general_06_05`
- 325 `783ae14efe7e9f3b` -> `mk_bp_general_04_01`
- 326 `c625e7606e7b7d5f` -> `mk_bp_general_03_10`
- 327 `e1c75879e8c5f3fa` -> `mk_bp_general_05_05`
- 328 `2cab8b18eb6b5cdf` -> `mk_bp_general_03_10`
- 329 `a435d43e4886372f` -> `mk_bp_general_04_01`
- 330 `ac583e2aeb2b5030` -> `mk_bp_general_04_06`
- 331 `e28270d69032991d` -> `mk_bp_general_06_04`
- 332 `6a1cab5f2dc54fbf` -> `mk_bp_general_01_05`
- 333 `d88c0e23219a7337` -> `mk_bp_general_09_02`
- 334 `1f6806ad7b00b72d` -> `mk_bp_general_09_04`
- 335 `0584d07c1241b4b0` -> `mk_bp_general_09_06`
- 336 `28df1f28ab88c7e2` -> `mk_bp_general_05_07`
- 337 `9855bf1df2c1d25e` -> `mk_bp_general_05_07`

## Verification

```bash
python3 scripts/fix/materialize_minimal_patch.py \
  --task question_set \
  --source output/mecnet-kokushi/questions_json/96/20_merged_1/question_96_merged.json \
  --raw "$TMP_RAW" \
  --output output/mecnet-kokushi/questions_json/96/22_questionSetId_linked/question_96_questionSetId_linked_20260609_2204.json
```

```bash
python3 scripts/check/check_question_set_patch_coverage.py \
  --source output/mecnet-kokushi/questions_json/96/20_merged_1/question_96_merged.json \
  --patch output/mecnet-kokushi/questions_json/96/22_questionSetId_linked/question_96_questionSetId_linked_20260609_2204.json \
  --category output/mecnet-kokushi/category/category.json \
  --questionset-only
```

```bash
python3 scripts/check/check_questionSetId.py \
  --category output/mecnet-kokushi/category/category.json \
  --original output/mecnet-kokushi/questions_json/96/00_source/question_96.json \
  --fixed output/mecnet-kokushi/questions_json/96/22_questionSetId_linked/question_96_questionSetId_linked_20260609_2204.json \
  --compare-count \
  --questionset-only
```

```bash
python3 scripts/merge/00_merge_all.py 96 -d output/mecnet-kokushi/questions_json
```

```bash
python3 scripts/check/report_mecnet_kokushi_full_pass_progress.py
```

## Results

- `raw_questionSetId_batch043_20260610_0030.json valid ids: 1`
- `occ96 raw first337 order ok: 337`
- `materialized 337 entries`
- `questionSet coverage: OK`
- `Original count: 337 / Fixed count: 337`
- `All questionSetId values are present in category.json`
- `merge_all completed for occ96`
- progress report after formalization: `01=7`, `02=7`, `03=6`, `04=2`, `20=7`

## Notes

- `96回` の prompt04 はこれで formal patch まで到達した。
- `30_merged_2` は explanation と `questionSetId` を反映して再生成された。
- 次は `97回` の prompt04 raw mapping に進める。
