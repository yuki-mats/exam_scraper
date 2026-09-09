# 問題整備システムの保留収束リファクタリング

## Objective

既存の一問queue、工程ごとの責務、`00_source`保護を維持しながら、保留再整備を停止工程から再開し、解消不能な入力不足を生成前に振り分け、集約回答の機械抽出漏れを直す。新しい仕組みを並立させず、現在の正常フローを単純にする。

## Original Request

「複雑化しないようにあるべき姿にシンプルなリファクタリングと改善を実施してほしい。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 問題整備システムの運用者と受験者
- Authority: `approved`
- Proof type: `test`, `metric`, `demo`
- Completion proof: 現行の保留715問を再現fixtureとして、停止工程からの再開、入力不足の事前停止、全角小文字を含む集約抽出がテストで証明され、代表previewで不要作業が5,005件から3,662件以下へ減り、品質・`00_source`・ID・Firestore境界が維持されること
- Goal oracle: 対象テスト、問題整備全体回帰、`00_source`不変検査、代表run/preview readback、変更前後の作業数と保留遷移
- Likely misfire: 新しいqueueや例外規則を増やす、保留を無理に合格へ変える、又は同じ入力のLLM再試行だけで解消しようとすること
- Blind spots considered: 旧run互換、工程版変更時の再実行、法令・画像取得の所有者、集約reviewのfail-closed境界、既存の未コミット成果物、同一fieldへの自動補正禁止
- Existing plan facts:
  - 保留再整備は問題ごとの最初の停止工程から開始する
  - 前工程を変更した場合だけ影響する後続工程を無効化する
  - 画像・本文・試験時法令などの不足はLLM前に検出し、必要入力が変わるまで再投入しない
  - 全角小文字 `ａ．` から始まる同一行の記述列を機械抽出できるようにする
  - 02aで02の不整合を検出した場合は自動補正せず、所有工程へ戻す
  - 変更ごとに実問fixture、回帰、`00_source`不変を検証し、`main`へcommit・pushする

## Goal Oracle

The oracle for this goal is:

`代表715問のblocked-rework previewで不要な前工程1,343件が消え、入力不足はmodel turn前に理由付き停止し、全角小文字の集約問題が原文spanだけから候補化され、既存品質gate・00_source・ID・Firestore非書込み境界をすべて通過する。`

PMは各receiptをこのoracleへ対応付ける。テスト追加だけ、作業数削減だけ、又は保留数を機械的に減らすだけでは完了としない。最終監査で`full_outcome_complete: true`を記録する。

## Goal Kind

`existing_plan`

## Current Tranche

既存queueの責務を増やさず、次の順で縦断的に改善する。第一にblocked-rework計画を問題別停止工程へ絞る。第二に集約回答の決定的抽出を表記揺れへ対応させる。第三にsource/evidence不足を候補生成前へ移す。必要な場合だけ、工程所有者へ戻す汎用的な依存情報を既存の機械検査結果へ追加する。

## Non-Negotiable Constraints

- `00_source`、既存question ID、既存Firestore IDを手修正しない。
- 一問queue、最大100問並列、一問内の工程順、問題別atomic patch、rollback、receiptを維持する。
- 機械検証は矛盾を検出して所有工程へ戻すが、どのfieldが正しいかを自動決定しない。
- 既存runをreadbackできる後方互換を維持する。移行書換えを行わない。
- 集約分解の本文はモデル生成せず、原文spanから機械的に切り出す。
- 入力不足を品質合格へ変更しない。再試行可能条件を明確にして保留を維持する。
- 本目標の検証でFirestoreへ問題データを書き込まない。
- 仕様は既存のworkflow設定・正本文書へ統合し、重複する状態機構や運用文書を作らない。
- 既存の未コミット成果物を破棄又は一括commitしない。変更範囲だけをscoped stagingする。
- ユーザー指示により、本goalではサブエージェントを新規起動せず、PMが直接調査・実装・検証・最終監査を行う。

## Stop Rule

Stop only when a final audit proves the full original owner outcome is complete.

安全な改善が残る限り、実装、検証、commit、`origin/main`へのpush、代表readbackを順番に完了する。保留を減らすために品質gateを緩和した場合、不要作業が残る場合、又は入力不足がmodelへ流れる場合は完了としない。

## Slice Sizing

一つのWorker packageは、運用者が確認できる一つの正常フロー改善を実装・テスト・文書・readbackまで閉じる。helper単位へ細分化せず、停止工程再開、集約抽出、入力準備の三つを基本sliceとする。

## Canonical Board

Machine truth lives at:

`docs/goals/question-maintenance-hold-convergence/state.yaml`

## Run Command

```text
Codex: /goal Follow docs/goals/question-maintenance-hold-convergence/goal.md.
Claude Code: /goalbuddy Follow docs/goals/question-maintenance-hold-convergence/goal.md.
```

## PM Loop

各継続時にこのcharter、GoalBuddy実行契約、`state.yaml`を読み、active taskだけを進める。各Worker後に対象回帰と実測を確認し、変更範囲だけをcommit・pushする。最終Judgeは代表715問の作業数、保留遷移、品質gate、`00_source`不変を原依頼へ対応付ける。
