# 00_source 全資格不変化と過去変更復元

## Objective

スクレイピングによる新規 `00_source` 作成は許可し、作成済み `00_source` の変更・削除・移動を全資格で禁止する。過去に直接変更された既存sourceは変更直前の内容へ戻し、修正結果は既存パッチ層で保持する。

## Original Request

スクレイピング時はOKだが、スクレイピング後の修正はパッチファイルのみでの更新として直接書き換えができないように、シンプルな仕組みを実装してほしい。変更してしまったところは元に戻しておいてほしい。

## Intake Summary

- Input shape: `recovery`
- Audience: exam_scraperのデータ作成・レビュー・Firestore公開担当者
- Authority: `requested`
- Proof type: `test`
- Completion proof: Git全履歴で確認した既存source変更19ファイルが変更直前の内容へ戻り、パッチ成果物が検証済みで、全source hash gateとpre-commit gateが既存変更・削除を拒否し新規追加だけを許可する。
- Goal oracle: 復元差分監査、全source manifest check、拒否/許可の自動テスト、資格別既存検証、Git同期。
- Likely misfire: 新規スクレイピングまで禁止する、過去の正答修正を失う、ignore配下のsourceをGit差分だけで守ったつもりになる。
- Blind spots considered: output配下の非追跡source、choiceText修正、source conflict metadata、cloneに存在しないDrive source、pre-commit未設定環境。
- Existing plan facts: 既存修正は10/15/21/22/23等の派生パッチで保持し、00_sourceは取得後read-onlyとする。

## Goal Oracle

`scripts/check/check_00_source_immutability.py --require-all` が全3,479 sourceで成功し、テストが既存変更・削除・移動を拒否して新規追加・manifest登録を許可し、過去変更19ファイルの復元後も対象資格のpatch coverageと公開gateが成功すること。

## Goal Kind

`recovery`

## Current Tranche

過去変更19ファイルを変更直前へ復元してパッチ保持を検証し、その復元済み状態をhash manifestへ固定して全資格共通ガードを導入する。

## Non-Negotiable Constraints

- 新規スクレイピングでの `00_source` 追加は許可する。
- 作成済み `00_source` の変更・削除・移動は禁止する。
- 復元前に修正内容が派生パッチへ残っていることを確認する。
- source本文・正答修正を失わない。
- mainのみを使用し、force pushしない。

## Stop Rule

最終監査が復元、パッチ保持、全source guard、テスト、Git同期を証明するまで完了扱いにしない。

## Canonical Board

Machine truth lives at:

`docs/goals/00-source-immutability-and-restoration/state.yaml`

## Run Command

```text
/goal Follow docs/goals/00-source-immutability-and-restoration/goal.md.
```

## PM Loop

1. active taskの範囲だけを変更する。
2. 復元時は変更直前blobと現在patchを比較する。
3. guard実装後は全source manifestと拒否/許可テストを実行する。
4. 最終Judge/PM監査でfull outcomeを確認する。
