# ガス主任技術者乙種 補助ドキュメント

このディレクトリは、`gas-shunin-otsu` の解説と法令根拠を作る際の資格固有の補助資料である。

## 使い分け

- [01_law_reference_policy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/gas-shunin-otsu/01_law_reference_policy.md)
  - ガス主任乙種で頻出する法令短縮表記、法令ID候補、`lawReferences` 作成時の注意点。

## 前提

- `03_prompt_add_explanationText.md` を正本とし、このディレクトリは資格固有の補助資料として読む。
- `explanation_choice_snippets` の `📌 関連:` は条文候補であり、最終的な `lawReferences.verificationStatus="verified"` にする前に e-Gov XML または官公庁一次情報で照合する。
- gassyunin.comのjudge欄から各記述を取得し、`choiceMarkerSource="judge"`、`markerAlignmentMode="judge_only"`、marker件数と選択肢件数が一致する問題では、`00_source.correctChoiceText`を記述別の正答証拠として扱う。`answer_result_text`の番号は元の組合せ肢を指すため、組合せ対応表がないことだけでは記述別正誤を保留しない。
