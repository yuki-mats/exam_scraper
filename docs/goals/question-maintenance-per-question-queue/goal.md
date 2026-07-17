# 一問完結型の問題整備キュー

## Objective

問題整備の開始範囲は資格・年度・回のまま維持し、選ばれた問題を一問単位の独立work itemとして並列に整備する。各問は「整備、検証、確定」まで独立させ、失敗はその問題と依存する後続工程だけに隔離し、成功済み問題を巻き戻しも再検証もしない。

## Original Request

「シンプルイズベスト。一問ずつ着実に整備できるようにし、開始範囲は資格・年度・回を維持する。一問ずつの作業は並列して整備する。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 問題整備を行う運用者
- Authority: `approved`
- Proof type: `demo`
- Completion proof: 58問のうち1問だけを意図的に失敗させても57問が確定し、失敗問だけを理由付きで再実行できる。再起動後も成功問を再処理せず継続でき、実際に問題整備UIからガス主任技術者甲種2019年を実行できる。
- Goal oracle: 一問ごとの処理回数・検証回数・確定状態を記録する統合テストと、問題整備UIから開始したガス主任技術者甲種2019年の実行ログ・成果物readback
- Likely misfire: 進捗表示だけを一問単位にし、receipt、commit、rollback、retry、最終検証をrun又は年度単位のまま残すこと
- Blind spots considered: 工程依存、入力変更時の再検証、二重実行、共有patchへの並列書込競合、成果物再生成の連打、解決不能な失敗の無限retry
- Existing plan facts: patch保存後の自動再生成、`artifactSync`の分離、手動再生成機能を維持する。`00_source`と無関係な変更には触れない。

## Goal Oracle

The oracle for this goal is:

`問題整備UIでガス主任技術者甲種2019年の58問を開始し、1問の整備又は検証が失敗しても他57問が一度だけ検証・確定される。失敗問は理由付きで保留され、再起動後にその1問だけを再実行でき、変更をまとめた成果物同期が収束する。`

PMは各receiptをこのoracleへ対応付ける。単体テストの一部成功、画面上の表示変更、又は「remainingなら除外できる」という間接証拠だけでは完了としない。最終監査で`full_outcome_complete: true`を記録する。

## Goal Kind

`existing_plan`

## Current Tranche

現行の範囲選択、human run、receipt、transaction、rollback、再開、`artifactSync`を調べ、範囲コンテナと一問単位のwork itemを分離する。各問の判断・修正案作成は隔離workspaceで並列実行し、共有patchへの適用・検証・確定だけを単一writerで安全に直列化する。次に、一問ごとの永続checkpoint、依存工程だけの停止、独立問題の継続、失敗問だけの再実行、変更分をまとめた成果物同期、状態を簡潔に示すUIを縦断実装する。最後に自動テストと再起動readbackを通し、問題整備UIからガス主任技術者甲種2019年を実行して実ログと成果物を確認する。

## Non-Negotiable Constraints

- 開始時の資格・年度・回の選択と、既存の対象previewを維持する。
- 選択範囲はqueueを作るためのコンテナであり、検証・確定・失敗・再実行の境界にしない。
- 一問は対象工程を順に完了し、その問題の検証に合格した時点で永続確定する。
- 一問の失敗で、成功済み問題をrollback、再生成、再検証しない。
- 失敗問の依存後続だけを止め、独立した問題は進め続ける。
- 入力又は評価方針のfingerprintが変わった問題だけを再検証対象に戻す。
- 解決不能な失敗は理由付き`blocked`とし、無限retryしない。
- patch writerは一つに保ち、再起動や重複要求でも二重確定しない。
- 一問ごとの判断と修正案作成は、共有成果物を書き換えない隔離workspaceで上限付き並列実行する。
- 並列workerは本体patchを直接変更しない。単一commit writerがexact identity単位で修正案を適用する。
- Merge・Convert・upload-readyは一問ごとに実行せず、確定済み変更をまとめて同期する。patch成功と`artifactSync`失敗は別状態のまま維持する。
- 手動再生成は非常用の導線として残す。
- 最終の全体検証はID重複や件数など安価な集合契約に限定し、全問の意味内容を再検証しない。
- 解説文の正本と品質基準を弱めない。
- `00_source`の既存ファイル、既存ID、本番Firestoreを変更しない。
- 既存のgas-shunin出力patchにある未コミット変更を破棄、上書き、混在commitしない。
- ガス主任技術者甲種2019年の実行前後を記録し、対象外年度のpatchを変更しない。既存の2019年差分は開始前状態を失わない。
- 仕様は既存の正本へ統合し、工程一覧や運用説明を重複させない。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

実装又はテストが一問単位に見えても、run単位rollback、全問再検証、失敗時の後続全停止が残る限り完了しない。安全なローカル作業が残る限り、次の最大安全な縦断sliceへ進む。本番Firestoreは明示承認がないため実行しない。

## Slice Sizing

最初のWorkerは、状態ファイルだけ、queueだけ、UIだけに分割せず、「複数の一問work itemが隔離workspaceで並列準備され、単一writerで一問ずつcheckpointされ、失敗後も他問が進み、再起動後に失敗問だけ再開できる」縦断sliceを優先する。成果物同期とUIも同じ安全境界へ統合する。

## Canonical Board

Machine truth lives at:

`docs/goals/question-maintenance-per-question-queue/state.yaml`

## Run Command

```text
/goal Follow docs/goals/question-maintenance-per-question-queue/goal.md.
```

## PM Loop

各継続時にこのcharter、GoalBuddy実行契約、`state.yaml`を読み、active taskだけを進める。各完了又は停止はreceiptへ残し、oracleが満たされるまで次の安全なtaskへ進む。
