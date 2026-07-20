# ガス主任技術者甲種 補助ドキュメント

このディレクトリは、`gas-shunin-kou` の解説と法令根拠を作る際の資格固有の補助資料である。

## 使い分け

- [01_law_reference_policy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/gas-shunin-kou/01_law_reference_policy.md)
  - ガス主任甲種で頻出する法令短縮表記、法令ID候補、`lawReferences` 作成時の注意点。

## 前提

- `03_prompt_add_explanationText.md` を正本とし、このディレクトリは資格固有の補助資料として読む。
- `explanation_choice_snippets` の `📌 関連:` は条文候補であり、最終的な `lawReferences.verificationStatus="verified"` にする前に e-Gov XML または官公庁一次情報で照合する。
- 甲種でも法令問題は現行法と出題当時法令を突き合わせる。現行法で正誤が明らかに変わる場合は現行法ベースへ更新し、更新済み注記、出題当時正答との差分、`current_basis` / `exam_time_basis`、review sidecar を残す。
