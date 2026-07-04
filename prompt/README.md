# prompt

過去問整備の目視作業で使うプロンプト置き場です。

## 日々の基本順序

1. `01_prompt_fix_questionType.md`: 問題形式を決める。
2. `02_prompt_fix_questionIntent.md`: 正しいものを選ぶ問題か、誤っているものを選ぶ問題かを決める。
3. `03_prompt_add_explanationText.md`: 基本解説、想定質問、法令参照を整える。
4. `04_prompt_link_questionSetId.md`: `category.json` を見て問題集へ紐付ける。

この目視作業が品質判断の本体です。スクリプトは、patch 形式の補完、merge、convert、検証、upload dry-run を効率化するために使います。

## 資格固有資料

資格ごとの出題範囲、解説方針、カテゴリ粒度、法令スコープは `qualification_docs/<qualification>/` に置きます。共通フィールドの意味は資格ごとに変えず、必ず `document/reference/question_field_contract.md` に従ってください。

## 機械チェック

patch 作成後は、個別 script を探さず統一CLIを使います。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```
