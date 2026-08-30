# ガス主任技術者乙種 補助ドキュメント

このディレクトリには、`gas-shunin-otsu`の法令根拠と必要最小限の解説調整を置く。

## 使い分け

- [01_law_reference_policy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/gas-shunin-otsu/01_law_reference_policy.md)
  - ガス主任乙種で頻出する法令短縮表記、法令ID候補、`lawReferences` 作成時の注意点。

## 解説の資格固有調整

- `03_prompt_add_explanationText.md`を共通の正本とする。
- `explanation_choice_snippets` の `📌 関連:` は条文候補であり、最終的な `lawReferences.verificationStatus="verified"` にする前に e-Gov XML または官公庁一次情報で照合する。
- 問題文・選択肢・公式正答は、同年度乙種のJIA公式問題PDFと公式正答PDFを一問ずつ目視して確定する。PDFページ、冊子ページ、科目、問番号、正答番号を作業証跡へ残す。
- gassyunin.comの本文、judge欄、`answer_result_text`、`00_source.correctChoiceText`は最終正誤の根拠にしない。同サイトを問題整備又は監査のために閲覧しない。
- 記述別問題へ分解する場合は、公式問題PDF上の全記述と組合せ肢を公式正答番号へ対応させてから、各記述の正誤を確定する。対応を一意に確定できない問題は`hold`にする。
- 類題と既存解説は説明の観点・粒度を整えるために参照できるが、元問題の本文、選択肢、正答を確定する根拠にはしない。
