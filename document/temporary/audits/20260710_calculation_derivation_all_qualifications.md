# 全資格 計算問題導出監査サマリ

監査日: 2026-07-10

## 目的

全資格の `questions_json` 配下にある統合済み問題から計算問題候補を洗い出し、基本の解説である `explanationText` に、初心者が読んでも追える導出計算が含まれているかを確認する。

今回の基準では、単に数式記号が含まれているだけでは十分としない。少なくとも、公式または考え方、問題文の数値代入、途中式、単位換算、最終的な選択肢への接続が見えることを要件にした。

## 実施方針

- 対象は `output/*/questions_json/*/30_merged_2/*.json`。
- 計算問題候補の抽出は `scripts/check/audit_calculation_explanations.py` を使う。
- 初心者向け導出の判定は `scripts/check/audit_beginner_calculation_explanations.py` を使う。
- 判定フラグは `hasFormulaIntro`、`hasSubstitution`、`hasStepMarker`、`hasUnit`、`hasAnswerReason`。
- 症例文、法令文、単なる統計値・基準値の知識問題、式の選択問題は、計算問題候補から除外する。
- `gas-shunin-all` は集約用で `30_merged_2` がないため、実体のある `gas-shunin-kou` と `gas-shunin-otsu` を監査対象とする。
- 外部 AI / LLM への照会は使わず、ローカル JSON、ローカル画像、既存ソース情報、決定的な監査スクリプトで確認した。

## 監査結果

初心者向け導出監査の最終結果は、全資格横断の計算問題候補 515 件、導出不足 0 件。

最小監査では「`explanationText` に式・数式記号・導出語があるか」を中心に見ており、現在値は 517 件中不足 0 件。今回の初心者向け監査では、式選択問題などをさらに除外して基準を上げ、式の根拠、代入、途中式、単位、結論まで確認したうえで不足を補正した。

| 資格 | 計算問題候補 | 導出不足 |
|---|---:|---:|
| 2dobokusekou | 11 | 0 |
| 2nd-class-kenchikushi | 43 | 0 |
| gas-shunin-kou | 76 | 0 |
| gas-shunin-otsu | 89 | 0 |
| judoseifukushi | 2 | 0 |
| kaigofukushi | 1 | 0 |
| kougai | 154 | 0 |
| kounin-shinrishi | 1 | 0 |
| kyusuikouji-shunin | 19 | 0 |
| mecnet-kokushi | 74 | 0 |
| nw | 21 | 0 |
| tsukanshi | 24 | 0 |

## 主な補正内容

- `2dobokusekou`、`2nd-class-kenchikushi`、`gas-shunin-otsu`、`kougai`、`kyusuikouji-shunin`、`mecnet-kokushi`、`tsukanshi` の計算問題で、基本の `explanationText` に導出計算を追加した。
- `suggestedQuestions` と `suggestedQuestionDetails` は、必要に応じて公式、単位換算、代入、正答根拠に寄せた。
- 二級建築士 `85009` は、解説上の計算結果と正答がずれていたため、`correctChoiceText` と `answer_result_text` も補正した。
- 公害防止管理者 2020 年 `4789029ea9a43425` は図依存の SRT 問題だったため、ローカル画像 `output/kougai/question_images/2020/kougai_yaku-tik_kougai_r2-osui-13_img01.png` を確認し、図中の数値を使って導出した。

## 生成物

- 初心者向け監査サマリ JSON: `output/reports/calculation_derivation_audit/beginner_summary.json`
- 初心者向け監査 JSONL: `output/reports/calculation_derivation_audit/beginner_calculation_derivation_audit.jsonl`
- 最小監査サマリ JSON: `output/reports/calculation_derivation_audit/all_qualifications_summary.json`
- 最小監査 JSONL: `output/reports/calculation_derivation_audit/all_qualifications_calculation_derivation_audit.jsonl`

`output/` は git ignore 対象のため、上記の JSON/JSONL はローカル検証成果物として扱う。

## 実行コマンド

```bash
roots=($(find output -maxdepth 2 -type d -name questions_json | sort))
args=()
for root in $roots; do args+=(--root "$root"); done
python3 scripts/check/audit_beginner_calculation_explanations.py "${args[@]}" \
  --jsonl output/reports/calculation_derivation_audit/beginner_calculation_derivation_audit.jsonl \
  --summary output/reports/calculation_derivation_audit/beginner_summary.json
```

最終サマリ:

```json
{
  "beginnerIssueCount": 0,
  "candidateCount": 515,
  "flagMissingCounts": {
    "hasAnswerReason": 0,
    "hasFormulaIntro": 0,
    "hasStepMarker": 0,
    "hasSubstitution": 0,
    "hasUnit": 0
  },
  "scoreDistribution": {
    "5": 515
  }
}
```
