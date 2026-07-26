# バッチ成果物モニターと安定イベントAPI

## Objective

現行の問題整備Python serverが起動・管理する単一Codex App Serverを対象に、問題整備runへ影響を与えない安定したread-onlyイベントAPIと、並列稼働・保存済み成果物・Agent発言・公開推論サマリー・活動イベントをリアルタイムで確認できる独立モニターUIを実装し、検証して`main`へ公開する。

## Original Request

「API基盤もあるべき姿に整備し、バッチ成果物モニターを構築してほしい。」

ユーザーは、問題整備システムからAgentへ指示する画面ではなく、バッチ成果物を文章で確認し、複数threadが並列稼働していること、Agentの公開された会話と推論サマリーから動いている様子を確認できるread-only画面を求めている。

## Intake Summary

- Input shape: `existing_plan`
- Audience: 問題整備バッチの運用者
- Authority: `approved`
- Proof type: `demo`
- Completion proof: 実run又は忠実なfixtureで、並列Lane、成果物本文、Agent発言、公開推論サマリー、tool・保存・検証・エラー状態、切断再接続を確認し、モニター有効・無効でmodel開始RPCとtoken usageが増えず、対象テスト、負荷試験、artifact readback、ブラウザwalkthroughが成功し、変更が`origin/main`へpushされている
- Goal oracle: 現行問題整備Python server配下の単一App Serverで実行されたrunをモニターからread-only観察し、表示と保存artifactが一致し、`monitorModelRequests=0`で、監視経路の障害がrunへ影響しないことを繰り返し確認できる
- Likely misfire: 汎用トレース画面、Agent操作画面、又は見栄えだけのmockを作り、保存済み成果物の確認、既存artifact正本、再接続、秘匿、追加tokenゼロを満たさない
- Blind spots considered: 既存未コミット変更、64並列時のbackpressure、App Server再起動時のgap、raw reasoningとsecretの秘匿、巨大manifestの反復読込、保存前のAgent出力と保存済み成果物の混同、monitor障害のrun波及、同一originのwrite権限混入
- Existing plan facts: 問題整備システムとモニターは別UI・別責務、初期は同じPython backendと単一App Server接続ownerを共有、将来は安定イベントAPIのconsumerとして別サービス化可能、Kamuiの暗色・高密度・並列時間軸のテイストを参考にするが操作・scheduler UIは持ち込まない、Langfuse等はMVPへ導入しない

## Goal Oracle

The oracle for this goal is:

`現行問題整備runの実イベントと保存artifactを使ったブラウザwalkthroughで、複数Laneの重なり、成果物本文、公開Agent活動、stale/gap、相互deep linkが正しく表示され、監視有無でmodel開始RPC・token usageが同一、monitorModelRequests=0、監視queue・保存・browser切断がrunを止めないことを自動試験とreceiptで証明する。`

PMは各Worker receiptをこのoracleへ照合する。APIだけ、静的UIだけ、単発fixtureだけ、又は一部テストの成功では完了しない。最終Judge又はPM監査が全証跡をoriginal outcomeへ対応付け、`full_outcome_complete: true`を記録した時だけ完了する。

## Goal Kind

`existing_plan`

## Current Tranche

既存の未コミット変更を保護・検証・整理した後、安定イベントAPI、read-onlyモニターUI、相互deep link、秘匿・再接続・負荷試験、正本文書、ブラウザwalkthrough、scoped commit/pushまでを連続して完了する。

## Non-Negotiable Constraints

- 初期監視対象は、現行の問題整備Python serverが起動・管理する1本のCodex App Server内のthreadに限定する。
- browserからApp Serverへ直接接続せず、現在のPython serverを唯一の接続ownerとする。別App Serverを起動しない。
- モニターにはAgent指示、開始、再実行、停止、interrupt、approval、編集、公開などのmutation機能を置かない。
- monitor APIには`thread/start`、`turn/start`、`turn/steer`、`turn/interrupt`等のmodel又はcontrol mutation clientを渡さない。
- モニター起因のmodel requestと追加token消費を0にする。LLMによる要約、分類、関連付け、評価を行わない。
- 表示する推論はApp Serverが公開した推論サマリーだけとし、raw reasoning、非公開の内部思考、system/developer instructionを表示・保存しない。
- toolの全引数、無加工stdout/stderr、環境変数、cookie、token、secret、絶対home pathを公開しない。本文はplain textとして安全に描画する。
- App Server受信threadではnon-blocking enqueueだけを行い、JSON加工、disk I/O、browser配信を実行しない。
- queue overflow、disk失敗、遅いbrowser、切断、monitor例外で問題整備runを停止・遅延・失敗させない。復元不能区間は`observationGap`として明示する。
- manifest、progress、result、receipt、保存成果物をworkflowの正本とし、monitor event storeとsnapshotは再構築可能な観測projectionに限定する。
- 実行状態、成果物状態、観測接続状態を別field・別表示にする。Agent完了だけで成果物を検証済みにしない。
- 親`runId`、`childRunId`、`questionId`又は`workItemKey`、`threadId`、`turnId`、`itemId`を役割に応じて使い、時刻又は本文類似で相関を推測しない。
- `00_source`を変更しない。既存workflow工程とartifact保存場所の正本を重複定義しない。
- UIは問題整備システムへ埋め込まず、独立したstatic assetとread-only routeにする。相互遷移はstable IDを使うdeep linkだけにする。
- MVPではLangfuse、LangSmith、Phoenix等の外部trace基盤を導入しない。将来export可能な自前`monitor-event/v1`境界を先に完成させる。
- Gitは`main`だけを使い、既存変更を破棄せず、内容別のscoped commitを`origin/main`へpushする。
- 実装開始前に現在の未コミット変更を監査し、所有範囲、検証結果、今回の変更との重なりを確定する。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, API schema作成、backend単体、UI mock、又は一つのWorker packageだけで、安全な後続作業が残っている場合は次のlargest safe sliceへ進む。

権限、外部接続、破壊的操作又は人間判断が一部sliceをblockしても、残るlocal・non-destructive作業を継続する。人間の正確な承認だけが唯一の残存blockerになった場合は、その文言をreceiptへ保存して一度だけ確認する。

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

backendはevent ingestion、stable schema、projection、再接続API、秘匿、token-zero検証までを一つのvertical sliceとして扱う。frontendはRun/Lane、成果物Reader、Agent実況、接続状態、deep link、responsive・accessibilityを動く画面としてまとめる。helper単位の細切れtaskを増やさない。

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.1/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/batch-artifact-monitor
```

ローカルboardが動いている場合は`state.yaml`とboard APIを照合する。active task以外のproduct fileを編集せず、GoalBuddy control fileとtask receiptの整合を保つ。

## Canonical Board

Machine truth lives at:

`docs/goals/batch-artifact-monitor/state.yaml`

## Run Command

```text
/goal Follow docs/goals/batch-artifact-monitor/goal.md.
```

## PM Loop

1. このcharter、GoalBuddy execution contract、`state.yaml`を読む。
2. original request、oracle、既存変更、non-negotiable constraintsを再確認する。
3. active taskだけをScout、Judge、Worker又はPMへ割り当てる。
4. 各taskへreceiptを残し、PMだけがboard stateと次のactive taskを更新する。
5. phase、risk、rejected verification、ambiguity、final boundaryだけでJudge reviewを行う。
6. safe local workが残る限り、次のlargest safe Worker packageへ進む。
7. 最終監査で全receipt、verification、browser walkthrough、git readbackをoracleへ対応付け、`full_outcome_complete: true`を記録する。
