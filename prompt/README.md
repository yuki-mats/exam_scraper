# prompt

過去問整備の目視作業で使うプロンプト置き場です。

## 日々の基本順序

1. `01_prompt_fix_questionType.md`: 問題形式を決める。
2. `02_prompt_fix_questionIntent.md`: 正しいものを選ぶ問題か、誤っているものを選ぶ問題かを決める。
3. `02b_prompt_prepare_law_context.md`: 03の前に、厳密な `isLawRelated` と現行法根拠候補を整理する。
4. `03_prompt_add_explanationText.md`: 02bの法令コンテキストを使って、基本解説、想定質問、保存済み回答を整える。
5. `03b_prompt_audit_current_law_and_patch.md`: 法改正・現行法差分が疑われる問題、または年1回の法令関係問題の全問監査で、03bの監査パッチ/sidecarを作成・更新し、既存成果物へマージする。
6. `04_prompt_link_questionSetId.md`: `category.json` を見て問題集へ紐付ける。

この目視作業が品質判断の本体です。スクリプトは、patch 形式の補完、merge、convert、検証、upload dry-run を効率化するために使います。

## 法改正・現行法差分監査

通常は03の前に02bで `18_law_context_prepared/` を作り、mergeで `20_merged_1/` に反映します。03は、その `isLawRelated`、`lawGroundedExplanationNotNeeded`、`lawReferences`、必要なら `lawContextForExplanation` を使って、解説本文と想定質問を作ります。

03または02bで法改正・現行法差分が疑われる問題を見つけたら、03bの監査パッチ/sidecarを作成・更新します。その後、03bの判断結果を `15_correctChoiceText_fixed` / `23_correctChoiceText_fixed` / `21_explanationText_added` などの既存成果物へマージします。年に1度、法令が関係する問題を全問監査して更新する場合も同じ流れです。

現行法ベースへ正誤・解説を更新した問題では、ユーザーが「出題当時の正答」と「現行法ベースの学習上の扱い」を区別できるように、`explanationText`、`suggestedQuestions`、`suggestedQuestionDetails`、`lawReferences`、年次監査 sidecar に注記と根拠を残します。

02b以降では、全問題に `isLawRelated` を必ず付けます。`isLawRelated` は法令・制度論点かどうかの正本フラグで、`lawGroundedExplanationNotNeeded` は原則その逆です。03はこの判定を文章化に使い、年次03b監査は `isLawRelated=true` の問題を起点にします。

## 資格固有資料

資格ごとの出題範囲、解説方針、カテゴリ粒度、法令スコープは `qualification_docs/<qualification>/` に置きます。共通フィールドの意味は資格ごとに変えず、必ず `document/reference/question_field_contract.md` に従ってください。

## 機械チェック

patch 作成後は、個別 script を探さず統一CLIを使います。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id> \
  --require-law-context-stage \
  --require-is-law-related \
  --require-law-grounded-flag
```
