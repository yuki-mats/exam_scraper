# ガス主任技術者 全問通常フロー整備・Firestore公開

## Objective

ガス主任技術者試験の甲種・乙種、2017〜2025年の全問題を、個別hotfixではなく既存の問題整備システムの通常フローへ一本化する。`00_source`を保護したまま正答・解説・分類を再確認し、merge、convert、品質ゲート、Firestore反映、全件readbackまで完了する。

## Original Request

「通常フローへ一本化します。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: ガス主任技術者試験の受験者と問題運用者
- Authority: `approved`
- Proof type: `source_backed_answer`
- Completion proof: 甲種・乙種2017〜2025年の全対象が通常フローの現行工程を通過し、正答・解説・ID・Firestore liveデータが公開候補と一致すること。
- Goal oracle: `00_source`不変、全対象の正答・解説整合、正答差分の根拠付き承認、全品質ゲート成功、Firestore全対象readback一致、報告対象の乙種2024年基礎理論問9が4.3を正解として正常採点されること。
- Likely misfire: 対象5documentだけを個別投入する、通常フロー外の一時ロジックを増やす、全件処理済みという件数だけで正答・解説品質を完了扱いにする、既存ID又は`00_source`を変更する。
- Blind spots considered: 旧Goalは934問を対象としており、現在の全18年度群を表していない。`00_source`と整備後正答に既知差分があり、差分は自動補正せず個別根拠で判断する。Firestore公開は差分documentだけを書き、全対象を読み戻す。
- Existing plan facts: `00_source`を基本の正本とする。正答と解説を照合する。`00_source`と整備後正答の差分は妥当性を確認する。最後にFirestoreへ反映する。個別5件の先行uploadは行わない。全問整備の開始と工程操作は問題整備システムのUIから行う。

## Goal Oracle

The oracle for this goal is:

`甲種・乙種2017〜2025年の全対象について、00_source不変、通常工程の現行版、正答と解説の矛盾0件、未承認の00_source正答差分0件、ID drift 0件、品質ゲート成功、Firestore readback不一致0件を同一run receiptで証明し、乙種2024年基礎理論問9の正答4.3とflash_card採点をliveで確認する。`

PMは各task receiptをこのoracleと照合する。調査、局所修正、dry-run、upload command成功だけでは完了しない。最終Judge又はPM監査が`full_outcome_complete: true`を記録した場合だけ完了とする。

## Goal Kind

`existing_plan`

## Current Tranche

甲種・乙種の2017〜2025年、全18年度群を一つの公開単位として扱う。現物inventoryで問題数とFirestore document数を再確定し、乙種全年度、甲種全年度、全体差分監査、公開候補再生成、Firestore公開・readbackの順に連続実行する。

## Non-Negotiable Constraints

- 常に日本語で報告する。
- `00_source`の内容・ファイル名を変更、削除、改名しない。
- 既存`questionId`、`originalQuestionId`、`questionSetId`を理由なく変更しない。
- `00_source`と整備後正答が異なる場合は、両者を独立に確認し、根拠のある例外だけpatchに残す。
- 正答と解説は全選択肢単位で照合し、表記ゆれと意味上の矛盾を区別する。
- 問題整備システムの通常工程、merge、convert、quality-gate、upload dry-runを省略しない。
- 全問整備の対象選択、開始、再開、状況確認、評価、Firestore反映は問題整備システムのUIから操作する。CLIでUIの工程制御を迂回しない。
- CLIはUIが生成した成果物のread-only検証、`00_source`不変性確認、Git確認、Firestore readbackの補助に限る。
- 個別5documentの先行hotfix artifactを本番投入しない。
- Firestoreには全候補を無条件上書きせず、実差分documentだけを書き込む。
- Firestore書込み直前にproject ID、artifact hash、差分件数、ID不変性、live同時更新を再確認する。
- upload後は全対象をreadbackし、候補との不一致0件を確認する。
- 別作業の未コミット差分を変更、stage、commit、revertしない。
- 通常フローに不足が見つかった場合は、資格固有の一時分岐を増やさず、責務に合う既存工程で解消する。

## Stop Rule

最終監査がowner outcome全体を満たすと証明した場合だけ停止する。

計画、inventory、単一年度、対象5件の修正、dry-run、Firestore upload command成功だけでは停止しない。安全に続けられる通常フロー作業が残る場合は次のtaskへ進む。

外部状態又は一時障害で一部が止まっても、実行可能な他の年度群、検証、artifact生成を継続する。Firestoreの同時更新、資格・年度・ID不一致、`00_source`変化、未承認の正答差分、正答と解説の意味的矛盾があれば、その公開工程は停止して根拠確認へ戻す。

## Slice Sizing

乙種全年度、甲種全年度、全体差分監査、全体公開候補生成、Firestore公開を、それぞれ最大の安全な一括sliceとして扱う。一問ごとのWorker/Judge対は作らず、問題単位の証跡をslice receipt内へ集約する。

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.1/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/gas-shunin-full-normal-flow-publish
```

## Canonical Board

Machine truth lives at:

`docs/goals/gas-shunin-full-normal-flow-publish/state.yaml`

## Run Command

```text
/goal Follow docs/goals/gas-shunin-full-normal-flow-publish/goal.md.
```

## PM Loop

1. `goal.md`と`state.yaml`、GoalBuddy execution contractを読む。
2. active taskだけを扱う。
3. 各Worker sliceの前後で`00_source`不変性とdirty fingerprintを確認する。
4. 問題整備システムのUIから対象を選択し、開始・再開・状況確認・評価・公開を操作する。
5. UIが呼ぶ通常フローを使い、既存工程を省略しない。CLIで工程状態を直接進めない。
6. task receiptへ対象年度群、問題数、変更file、検証、差分、hold、Firestore反映有無を記録する。
7. risk、phase、公開直前、最終完了の境界だけJudgeレビューを行う。
8. 最終監査でoracle全項目を一つずつreadbackへ対応付ける。
