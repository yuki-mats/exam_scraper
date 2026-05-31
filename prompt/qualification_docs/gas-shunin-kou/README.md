# ガス主任技術者甲種 補助ドキュメント

このディレクトリは、`gas-shunin-kou` の `explanationText` / `suggestedQuestions` / `lawReferences` を作る際の補助資料である。

## 使い分け

- [01_law_reference_policy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/gas-shunin-kou/01_law_reference_policy.md)
  - ガス主任甲種で頻出する法令短縮表記、法令ID候補、`lawReferences` 作成時の注意点。

## 前提

- `03_prompt_add_explanationText.md` を正本とし、このディレクトリは資格固有の補助資料として読む。
- `explanation_choice_snippets` の `📌 関連:` は条文候補であり、最終的な `lawReferences.verificationStatus="verified"` にする前に e-Gov XML または官公庁一次情報で照合する。
- 甲種でも「過去問の元の正誤」より「現行法をどう学ぶか」を優先する。出題当時法令が必要な場合だけ `exam_time_basis` を補助的に追加する。
