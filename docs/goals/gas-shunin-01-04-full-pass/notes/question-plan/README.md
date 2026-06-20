# Gas Shunin 01-04 Per-Question Execution Plan

この計画は review ledger の各1行を1問として、全934問を一問ずつ処理するための実行キューです。
`00_source` の本文・選択肢は編集対象外で、`originalQuestionBodyText` / `originalQuestionChoiceText` と既存 Firestore document ID を保持します。
`explanationText` と suggested 系は、本文・選択肢を変更しない範囲で補完対象です。

## Files

- `all_questions_plan.jsonl`: 1行=1問の正本計画
- `all_questions_plan.tsv`: 目視確認用の一覧
- `summary.json`: 件数集計

## Counts

- Total: 934
- gas-shunin-kou: 412 / decisions {'pending': 405, 'ok': 7}
- gas-shunin-otsu: 522 / decisions {'pending': 522}

## Execution Phases

- Phase 1: 甲種 Firestore既存IDあり: 292
- レビュー済み: 2
- Phase 2: 甲種 gassyunin.com/新規系: 118
- Phase 3: 乙種 全source: 522

## Per-Question Contract

各問は次の順で処理します。

1. `00_source` を読んで questionType が設問形式と一致するか確認する。
2. 設問文から questionIntent を確定し、correctChoiceText が選択肢位置と一致するか確認する。
3. explanationText / suggestedQuestions / suggestedQuestionDetails を補完する。本文・選択肢は変更しない。
4. Firestore category 由来の questionSetId を確定する。
5. 対象行検証、file-level coverage、review ledger check を通す。
6. 該当 review ledger 行だけを `ok` または理由付き `hold` にする。

## Stop Conditions

- `00_source` の本文・選択肢を直したくなる場合。
- 既存 Firestore document ID の対応が曖昧な場合。
- correctChoiceText の根拠が不足する場合。
- questionSetId のカテゴリ判断が曖昧な場合。
- explanationText が未検証の事実を必要とする場合。

## Next Execution

Next pending: sequence 6 / gas-shunin-kou 2019 問14 / `firestore:chiefgasengineerlicense-A-80-1552,chiefgasengineerlicense-A-80-1553,chiefgasengineerlicense-A-80-1554,chiefgasengineerlicense-A-80-1555,chiefgasengineerlicense-A-80-1556`
