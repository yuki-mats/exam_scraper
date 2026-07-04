# prompt

過去問整備の目視作業で使うプロンプト置き場です。

## 日々の基本順序

1. `01_prompt_fix_questionType.md`: 問題形式を決める。
2. `02_prompt_fix_questionIntent.md`: 正しいものを選ぶ問題か、誤っているものを選ぶ問題かを決める。
3. `03_prompt_add_explanationText.md`: 基本解説、想定質問、法令参照を整える。
4. `03b_prompt_audit_current_law_and_patch.md`: 法改正・現行法差分が疑われる問題、または年1回の法令関係問題の全問監査で使う。
5. `04_prompt_link_questionSetId.md`: `category.json` を見て問題集へ紐付ける。

この目視作業が品質判断の本体です。スクリプトは、patch 形式の補完、merge、convert、検証、upload dry-run を効率化するために使います。

## 法改正・現行法差分監査

通常の03では、基本解説と想定質問を作りながら、法改正・現行法差分が疑われる問題を見つけたら 03b へ切り出します。年に1度、法令が関係する問題を全問監査して更新する場合も 03b を使います。

現行法ベースへ正誤・解説を更新した問題では、ユーザーが「出題当時の正答」と「現行法ベースの学習上の扱い」を区別できるように、`explanationText`、`suggestedQuestions`、`suggestedQuestionDetails`、`lawReferences`、年次監査 sidecar に注記と根拠を残します。

## 資格固有資料

資格ごとの出題範囲、解説方針、カテゴリ粒度、法令スコープは `qualification_docs/<qualification>/` に置きます。共通フィールドの意味は資格ごとに変えず、必ず `document/reference/question_field_contract.md` に従ってください。

## 機械チェック

patch 作成後は、個別 script を探さず統一CLIを使います。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```
