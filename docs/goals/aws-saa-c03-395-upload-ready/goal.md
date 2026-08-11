# AWS SAA-C03 残り395問の整備・upload-ready収束

## Objective

`aws-solutions-architect-associate` の SAA-C03 残り395問を、既存の問題整備システムと正本ワークフローだけで一問ずつ整備し、Firestoreへ書き込まずに公開直前の upload-ready・quality gate・upload dry-run まで収束させる。

## Original Request

「あと395問です。CLF-C02は879問完了、SAA-C03は残り395問。AWS系全体1,274問の残りを整備してほしい。」

## Intake Summary

- Input shape: `recovery`
- Audience: AWS Certified Solutions Architect - Associate（SAA-C03）を学習する受験者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: 395問すべてについて必要工程が完了し、merge・convert・upload-readyが一致し、quality gateとupload dry-runが成功した終端receiptが残ること。
- Goal oracle: 現行 `QuestionInventory` で SAA-C03 が395/395 local-readyとなり、終端runの `status=succeeded`、`verified=true`、`artifactSync=succeeded`、hold/pending/blocked=0、Firestore writes=0、`00_source`不変が一致すること。
- Likely misfire: 395件のファイル生成だけで整備完了とみなす、問題整備システムを迂回する、05又は03だけで止める、local-readyをFirestore公開済みと混同する、又は依頼のないFirestore書込みを行うこと。
- Blind spots considered: 既存runとrepository lock、Udemy由来問題の05独自問題化、独自画像gate、category.jsonと04、既存ID維持、hold/pending再開、artifact sync、現在のFast利用可否、Firestore書込み権限。
- Existing plan facts: CLF-C02は879問完了として扱い変更しない。SAA-C03のローカル対象は395問。AWS系全体の目標件数は1,274問。問題整備システムを正規入口とする。

## Goal Oracle

The oracle for this goal is:

`SAA-C03 395/395 local-ready + 終端run成功・検証済み + artifact sync成功 + quality gate成功 + upload dry-run成功 + hold/pending/blocked=0 + Firestore writes=0 + 00_source不変 + Git main push readback`

PMは各task receiptをこのoracleと照合する。調査、計画、途中工程の成功又はファイル数だけでは完了しない。最終Judge/PM監査がユーザーの元の依頼まで証拠を対応付け、`full_outcome_complete: true` を記録したときだけ完了する。

## Goal Kind

`recovery`

## Current Tranche

現物から395問の再開地点と必要工程を確定し、既存成果物を保持したまま、最大の安全な一括Worker sliceで問題整備runを収束させる。その後、公開前成果物、全gate、dry-run、非書込み、`00_source`不変、Git同期を確認し、AWS全体1,274/1,274準備済みを監査する。

## Non-Negotiable Constraints

- `00_source` の内容・ファイル名・相対配置を手作業又はAIで変更・削除しない。
- 既存の `questionId`、`originalQuestionId`、`questionSetId` を理由なく変更しない。
- Udemy由来のSAA-C03は、現行設定に従って必要な05独自問題化と画像gateを省略しない。
- 一問は必要工程を順に閉じ、問同士は可能な範囲で独立に進める。
- 判断不能は推測で閉じず、`hold`又はreview sidecarへ送る。ただし最終完了にはhold/pending/blocked=0が必要。
- merge、convert、upload-readyを直接編集しない。正本patchから正規処理で再生成する。
- Firestoreへの書込みはこの依頼に含めない。upload dry-runとread-only inventoryまでで止める。
- CLF-C02の完了済み879問を再整備又は変更しない。
- `main` だけを使い、関連変更ごとに検証・commit・`origin/main`へpushする。

## Stop Rule

最終監査が元の依頼全体の完了を証明した場合だけ停止する。

調査、計画又はJudgeの選定後、安全なWorker taskが存在するなら停止しない。Worker packageが一つ終わっても395問全件のoracleが満たされなければ、次の安全なpackageへ進む。

人間承認、認証情報、本番権限又は破壊的操作が必要なsliceだけをblockedにし、残る安全なローカル作業を継続する。Firestore書込み承認は本目標の完了条件ではない。

## Slice Sizing

同じ工程・同じ保存契約の反復は、問題ごとにGoalBuddy taskを分けず、問題整備システムの一つのcoherent Worker packageとして実行する。Workerは指定した全対象と検証を完了し、Judgeはphase・risk・final boundaryだけで監査する。

## Board Health

盤の正本は `docs/goals/aws-saa-c03-395-upload-ready/state.yaml`。不整合時はGoalBuddy checkerを実行し、実装編集権限を持つactive taskがない限りcontrol filesだけを修復する。

## Canonical Board

Machine truth lives at:

`docs/goals/aws-saa-c03-395-upload-ready/state.yaml`

## Run Command

```text
Codex: /goal Follow docs/goals/aws-saa-c03-395-upload-ready/goal.md.
Claude Code: /goalbuddy Follow docs/goals/aws-saa-c03-395-upload-ready/goal.md.
```

## PM Loop

1. このcharterとGoalBuddy実行規約を読む。
2. `state.yaml`を唯一のtask状態として読む。
3. active taskだけを実行する。
4. Scout/Judge/Worker/PMのreceiptを即時記録する。
5. 安全な作業が残る限り次の最大のreversible sliceへ進む。
6. 各Worker後と最終時にoracleを再実行する。
7. host turn終了前に `check-can-stop.mjs` を通す。
