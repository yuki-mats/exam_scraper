# Gas Shunin 01-04 Per-Question Execution Plan

この計画は review ledger の各1行を1問として、全934問を一問ずつ処理するための実行キューです。
`00_source` の本文・選択肢は編集対象外で、`originalQuestionBodyText` / `originalQuestionChoiceText`、既存 Firestore document ID、`sourceQuestionKey` / `sourceUniqueKeys` を保持します。
`explanationText` と suggested 系は、本文・選択肢を変更しない範囲で補完対象です。

## Files

- `all_questions_plan.jsonl`: 1行=1問の正本計画
- `all_questions_plan.tsv`: 目視確認用の一覧
- `summary.json`: 件数集計

`reviewQuestionId` は資格内で一意です。横断処理では `qualifiedReviewQuestionId = qualification + ":" + reviewQuestionId` を使います。

## Counts

- Total: 934
- gas-shunin-kou: 412 / decisions {'pending': 412}
- gas-shunin-otsu: 522 / decisions {'pending': 522}
- Firestore source rows: 294
- Site source rows: 640
- Source-key conflict rows: 11
- Source conflict status: none 805 / metadata_resolved 4 / needs_source_review 125
- Firestore/site content conflicts: 441 fields across 121 canonical questions

Content conflicts are listed in `output/gas-shunin-kou/review/source_conflicts/firestore_site_conflicts.jsonl`.
The default upload gate fails while `needs_source_review` rows remain, unless the run explicitly passes `--allow-source-conflicts`.

## Execution Phases

- Phase 1: 甲種 Firestore既存IDあり: 294
- Phase 2: 甲種 gassyunin.com/新規系: 118
- Phase 3: 乙種 全source: 522

## Per-Question Contract

各問は次の順で処理します。

1. `00_source` を読んで questionType が設問形式と一致するか確認する。
2. 設問文から questionIntent を確定し、correctChoiceText が選択肢位置と一致するか確認する。
3. explanationText / suggestedQuestions / suggestedQuestionDetails を補完する。suggested 系は選択肢ごとの真偽・正誤に合わせ、問題単位で一括生成しない。
4. Firestore category 由来の questionSetId を確定する。
5. 対象行検証、file-level coverage、review ledger check を通す。
6. 該当 review ledger 行だけを `ok` または理由付き `hold` にする。

## Stop Conditions

- `00_source` の本文・選択肢を直したくなる場合。
- 既存 Firestore document ID の対応が曖昧な場合。
- correctChoiceText の根拠が不足する場合。
- questionSetId のカテゴリ判断が曖昧な場合。
- explanationText が未検証の事実を必要とする場合。
- suggestedQuestions を選択肢単位ではなく問題単位で処理しそうな場合。
- `sourceConflictStatus = needs_source_review` の行で、Firestore と gassyunin.com の差分を目視確認していない場合。

## Next Execution

Next pending: sequence 1 / gas-shunin-kou 2019 問1 / `firestore:chiefgasengineerlicense-A-40-1495,chiefgasengineerlicense-A-40-1496,chiefgasengineerlicense-A-40-1497,chiefgasengineerlicense-A-40-1498,chiefgasengineerlicense-A-40-1499`
