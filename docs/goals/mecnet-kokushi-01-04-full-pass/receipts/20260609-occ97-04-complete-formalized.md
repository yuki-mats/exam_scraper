# 2026-06-09 occ97 04 complete formalized

## Scope

- qualification: `mecnet-kokushi`
- occurrence: `97`
- step: `04_prompt_link_questionSetId.md`
- intent: `22_questionSetId_linked/wip` の raw mapping を source 順で完結させ、checker が読める formal patch に昇格する

## Local artifacts

- `output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip/raw_questionSetId_all_20260609_2325.json`
- `output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/question_97_questionSetId_linked_20260609_2325.json`
- `output/mecnet-kokushi/questions_json/97/30_merged_2/question_97_merged_20260609_2326.json`

## Final covered source questions

- source count: `363`
- raw entries: `363`
- formal patch entries: `363`
- final raw batch: `raw_questionSetId_batch046_20260609_2323.json`

## Verification

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

valid_ids = {
    item["questionSetId"]
    for item in json.loads(Path("output/mecnet-kokushi/category/category.json").read_text())["questionSets"]
}
for rel in [
    "output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip/raw_questionSetId_batch043_20260609_2323.json",
    "output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip/raw_questionSetId_batch044_20260609_2323.json",
    "output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip/raw_questionSetId_batch045_20260609_2323.json",
    "output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip/raw_questionSetId_batch046_20260609_2323.json",
]:
    rows = json.loads(Path(rel).read_text())
    bad = [row for row in rows if row["questionSetId"] not in valid_ids]
    print(Path(rel).name, "valid ids:", len(rows) - len(bad))
    if bad:
        raise SystemExit(bad)
PY
```

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

source = json.loads(Path("output/mecnet-kokushi/questions_json/97/20_merged_1/question_97_merged.json").read_text())["question_bodies"]
expected = [q["original_question_id"] for q in source[:363]]
paths = sorted(Path("output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip").glob("raw_questionSetId_batch*.json"))
rows = []
for path in paths:
    rows.extend(json.loads(path.read_text()))
actual = [row["original_question_id"] for row in rows[:363]]
if len(rows) != 363:
    print("row count mismatch", len(rows))
    raise SystemExit(1)
if actual != expected:
    for i, (a, e) in enumerate(zip(actual, expected), start=1):
        if a != e:
            print("mismatch", i, a, e)
            break
    raise SystemExit(1)
print("occ97 raw all order ok:", len(actual))
PY
```

```bash
.venv/bin/python scripts/fix/materialize_minimal_patch.py \
  --task question_set \
  --source output/mecnet-kokushi/questions_json/97/20_merged_1/question_97_merged.json \
  --raw output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/wip/raw_questionSetId_all_20260609_2325.json \
  --output output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/question_97_questionSetId_linked_20260609_2325.json
```

```bash
.venv/bin/python scripts/check/check_question_set_patch_coverage.py \
  --source output/mecnet-kokushi/questions_json/97/20_merged_1/question_97_merged.json \
  --patch output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/question_97_questionSetId_linked_20260609_2325.json \
  --category output/mecnet-kokushi/category/category.json \
  --questionset-only
```

```bash
.venv/bin/python scripts/check/check_questionSetId.py \
  --category output/mecnet-kokushi/category/category.json \
  --original output/mecnet-kokushi/questions_json/97/00_source/question_97.json \
  --fixed output/mecnet-kokushi/questions_json/97/22_questionSetId_linked/question_97_questionSetId_linked_20260609_2325.json \
  --compare-count \
  --questionset-only
```

```bash
.venv/bin/python scripts/merge/00_merge_all.py 97 -d output/mecnet-kokushi/questions_json
```

```bash
.venv/bin/python scripts/check/report_mecnet_kokushi_full_pass_progress.py
```

## Results

- `raw_questionSetId_batch043_20260609_2323.json valid ids: 8`
- `raw_questionSetId_batch044_20260609_2323.json valid ids: 8`
- `raw_questionSetId_batch045_20260609_2323.json valid ids: 8`
- `raw_questionSetId_batch046_20260609_2323.json valid ids: 3`
- `occ97 raw all order ok: 363`
- `materialized 363 entries`
- `coverage check passed`
- `Original count: 363 / Fixed count: 363`
- `All questionSetId values are present in category.json`
- `merge_all completed for occ97`
- `questionSetId 更新件数: 363`
- progress report after formalization: `01=7`, `02=7`, `03=6`, `04=3`, `20=7`, `30=0`, `40=2`

## Notes

- `97回` の prompt04 はこれで formal patch まで到達した。
- `30_merged_2` は explanation と `questionSetId` を反映して再生成された。
- output 配下の generated JSON は `.gitignore` 対象のため、この receipt を tracked 証跡とする。
- Firestore upload はこの receipt では実施していない。
