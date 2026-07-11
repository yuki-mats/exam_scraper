# ガス主任技術者 法令261問 基本解説更新

## Objective

ガス主任技術者試験の法令261問を一問ずつ現行法で棚卸しし、基本解説、法令参照、lawRevisionFacts、suggested系を更新する。全問精査と公開成果物の完成後に限り、Firestore反映工程へ進む。

## Original Request

ガス主任技術者試験の法令問題について、一問ずつ棚卸しして基本解説を更新する作業を継続する。

## Intake Summary

- Input shape: `existing_plan`
- Audience: ガス主任技術者試験の受験者と運用者
- Authority: `requested`
- Proof type: `source_backed_answer`
- Completion proof: 261問すべてが公式e-Gov現行条文と照合済みとなり、pending・mapping error・decision error・未処理correctChoiceText flagが0で、公開成果物と最終検証が完成すること。
- Goal oracle: `summary.json`、`review_ledger.jsonl`、各decision、e-Gov根拠hash、17テスト、00_source非変更、mainとorigin/mainの一致。
- Likely misfire: 00_sourceや既存IDを変更する、複数問を一括判断する、誤り箇所を曖昧にする、全問完了前にFirestoreへアップロードする。
- Blind spots considered: 既存lawRevisionFactsの条番号誤り、空欄組合せ問題、現行条文で正答が変わる場合のflag運用、e-Gov APIの条・項・号粒度。
- Existing plan facts: mainのみ、一問一commit/push、00_source非変更、既存questionId保持、correctChoiceTextは原則正本、現行法と明白に矛盾する場合は修正せずflag、Firestoreは最終段階まで未反映。

## Goal Oracle

`output/gas-shunin-all/review/law_explanation_refresh/summary.json` が全261問の精査完了を示し、`review_ledger.jsonl` にpendingがなく、全decisionが適用済みで、指定17テスト、mapping/decision gate、00_source非変更、公開成果物監査、main/origin同期がすべて通ること。

## Goal Kind

`existing_plan`

## Current Tranche

次のpending問題を一問だけ、00_source確認、公式e-Gov照合、decision作成、適用、チャットで具体的解説表示、検証、commit、origin/main pushまで完了し、直ちに次のpendingへrolling taskを進める。

## Non-Negotiable Constraints

- `00_source`を変更しない。
- 既存questionIdを変更しない。
- mainだけを使い、新branch・worktree・force pushを行わない。
- 一問ごとに検証、commit、origin/main pushを行う。
- 誤り選択肢では誤っている部分、正しい内容、根拠条文位置を具体的に示す。
- 各問のpush前に、実際の`explanationText`をチャットへ表示する。
- Firestoreへは全261問精査と公開成果物完成後にまとめて反映する。

## Stop Rule

最終PM監査が全261問と公開成果物の完成を証明するまで完了扱いにしない。

## Canonical Board

Machine truth lives at:

`docs/goals/gas-shunin-law-explanation-refresh/state.yaml`

## Run Command

```text
/goal Follow docs/goals/gas-shunin-law-explanation-refresh/goal.md.
```

## PM Loop

1. charter、state.yaml、summary、ledger、git同期を確認する。
2. active taskの一問だけを公式e-Gov現行条文で精査する。
3. decisionを適用し、inventoryと17テストを実行する。
4. 00_source非変更と生成物readbackを確認する。
5. 具体的な5択解説をチャットへ表示する。
6. 一問分とboard receiptだけをcommitし、origin/mainへpushする。
7. 次のpending一問をactive taskにする。
