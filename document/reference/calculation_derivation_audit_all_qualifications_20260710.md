# 全資格 計算問題導出監査サマリ

監査日: 2026-07-10

## 目的

全資格の `questions_json` 配下にある統合済み問題から計算問題候補を洗い出し、基本の解説である `explanationText` に式、代入、単位換算、導出過程、結論が見える形で含まれているかを確認する。

## 判定方針

- 対象は `output/*/questions_json/*/30_merged_2/*.json`。
- 計算問題候補は `scripts/check/audit_calculation_explanations.py` で抽出する。
- `explanationText` に `=`, `×`, `÷`, `→`, `≒`, 数式、または導出を示す語がないものを不足候補とする。
- 症例文、法令文、単なる統計値・基準値の知識問題は、計算問題候補から除外する。
- `gas-shunin-all` は集約用で `30_merged_2` がないため、実体のある `gas-shunin-kou` と `gas-shunin-otsu` を監査対象とする。

## 監査結果

全資格横断の計算問題候補は 518 件、導出不足は 0 件。

| 資格 | 統合済みファイル数 | 統合済み問題数 | 計算問題候補 | 導出不足 |
|---|---:|---:|---:|---:|
| 2dobokusekou | 64 | 1,376 | 11 | 0 |
| 2nd-class-kenchikushi | 69 | 1,343 | 43 | 0 |
| anma | 165 | 3,958 | 0 | 0 |
| gas-shunin-all | 0 | 0 | 0 | 0 |
| gas-shunin-kou | 23 | 412 | 76 | 0 |
| gas-shunin-otsu | 27 | 522 | 89 | 0 |
| judoseifukushi | 176 | 4,240 | 2 | 0 |
| kaigofukushi | 100 | 2,078 | 1 | 0 |
| kougai | 96 | 2,160 | 154 | 0 |
| kounin-shinrishi | 65 | 1,375 | 1 | 0 |
| kyusuikouji-shunin | 35 | 585 | 19 | 0 |
| mecnet-kokushi | 52 | 13,060 | 77 | 0 |
| nw | 48 | 880 | 21 | 0 |
| sg | 1 | 1 | 0 | 0 |
| shinkyu | 104 | 2,390 | 0 | 0 |
| tsukanshi | 51 | 1,115 | 24 | 0 |

## 生成物

- サマリ JSON: `output/reports/calculation_derivation_audit/all_qualifications_summary.json`
- 行単位 JSONL: `output/reports/calculation_derivation_audit/all_qualifications_calculation_derivation_audit.jsonl`

`output/` は git ignore 対象のため、上記の JSON/JSONL はローカル検証成果物として扱う。

## 実行コマンド

```bash
roots=($(find output -maxdepth 2 -type d -name questions_json | sort))
args=()
for root in $roots; do args+=(--root "$root"); done
python3 scripts/check/audit_calculation_explanations.py "${args[@]}" \
  --jsonl output/reports/calculation_derivation_audit/all_qualifications_calculation_derivation_audit.jsonl \
  --summary output/reports/calculation_derivation_audit/all_qualifications_summary.json \
  --fail-on-issues
```
