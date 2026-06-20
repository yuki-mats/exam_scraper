# source canonicalization receipt

- 2010〜2025 の `output/kougai/questions_json/<year>/00_source/` を yaku-tik のみに整理した。
- 非 yaku-tik の `qualification-text` と `zoron` は `00_source_raw/` に退避した。
- canonical source は各年 6 ファイル、135 問で揃った。
- 合計は 96 ファイル、2,160 問である。

## 退避先

- `output/kougai/questions_json/<year>/00_source_raw/`

## 検証

```bash
for year in $(seq 2010 2025); do
  jq -s --arg year "$year" '[.[].question_bodies | length] | {year:$year,files:length,total:add,per_file:.}' \
    output/kougai/questions_json/$year/00_source/question_${year}_yakutik_*.json
done
```

```bash
find output/kougai/questions_json -path '*/00_source/*.json' -type f ! -name '*yakutik*' -print
```

