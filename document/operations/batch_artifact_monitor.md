# バッチ成果物モニター

この文書は、問題整備runを読取専用で観測するバッチ成果物モニターと、その安定イベントAPIの正本です。問題整備の実行・保存・公開手順は[問題整備システム](local_question_review_console.md)、保存場所は[artifact契約](artifact_contract.md)を参照してください。

## 目的と棲み分け

問題整備システムは、runの開始・再開、修正、評価、成果物再生成、Firestore反映など、共有状態を変更する操作を担います。

バッチ成果物モニターは、現行の問題整備Python serverが管理する一つのCodex App Server内のthreadだけを対象に、次の内容を表示します。

- 複数の問題・工程・threadが並行して動いている時間関係
- serverが保存した問題別成果物の本文と検証状態
- App Serverが公開したAgent発言と推論サマリー
- turn、tool、保存、検証、エラーなどの活動状態
- 実行状態、成果物状態、観測接続状態

モニターからAgentへ指示を出しません。開始、再実行、停止、interrupt、approval、編集、公開などの操作も置きません。問題整備システムとモニターは別画面・別責務とし、stable IDを含むdeep linkだけで相互に移動します。

## 構成

```text
Codex App Server
  │ public notification
  ▼
問題整備Python server
  ├─ 問題整備処理・正本artifact
  └─ non-blocking observer
       ├─ bounded queue
       ├─ monitor-event/v1 projection
       └─ GET-only monitor API
                         │
                         ▼
                  /monitor read-only UI
```

Python serverだけがCodex App Server接続を所有します。browserはApp Serverへ直接接続せず、モニター用に別App Serverも起動しません。observerにはApp Serverのrequest clientを渡さず、モニターAPIから`thread/start`、`turn/start`などを呼べない構造にします。

App Server受信threadで行うのは、allowlist対象notificationのbounded queueへの`put_nowait`だけです。正規化、秘匿、結合、disk保存、browser配信は別worker又はHTTP処理で行います。queue overflow、disk失敗、遅いbrowser、切断、monitor例外は観測欠落として扱い、問題整備runへ伝播させません。

## IDと相関

相関には、役割に応じて次のIDをそのまま使います。時刻や本文の類似から対応を推測しません。

| ID | 役割 |
| --- | --- |
| `runId` | 観測対象run |
| `parentRunId` | 一問queue全体の親run |
| `childRunId` | v1の子run、又はv2で一問attemptを相関する互換ID。v2の通常turnは子manifestを作らない |
| `questionId` / `workItemKey` | 問題又は作業項目 |
| `threadId` / `turnId` / `itemId` | App Server runtime |
| `serverInstanceId` / `sequence` | 観測eventの順序と再接続境界 |

問題整備側が`thread/start`と`turn/start`の応答で得たexact IDへ、開始時に渡したrun contextを結びます。parent、child、question、threadの関係をモニター独自の別IDへ置き換えません。

## `monitor-event/v1`

観測eventは次の共通envelopeを使います。

```json
{
  "schemaVersion": "monitor-event/v1",
  "eventId": "<serverInstanceId>:<sequence>",
  "serverInstanceId": "<opaque-id>",
  "sequence": 123,
  "observedAt": 0,
  "type": "agentMessage",
  "correlation": {
    "qualification": "gas-shunin-otsu",
    "parentRunId": "<run-id>",
    "childRunId": "<child-run-or-question-attempt-id>",
    "questionId": "<question-id>",
    "threadId": "<thread-id>",
    "turnId": "<turn-id>",
    "itemId": "<item-id>"
  },
  "payload": {}
}
```

公開するeventは、Agent発言、公開推論サマリー、turn lifecycle、tool名と状態、token usageなど、表示に必要なallowlist対象だけです。deltaは同じ`itemId`の連続部分を機械的に結合できます。modelを使った要約、分類、関連付け、補完は行いません。

cursorは`serverInstanceId`と`sequence`から作ります。browserは最後に確認したcursor以降を再取得します。memory上のreplay範囲外、queue overflow、server再起動などで連続性を証明できない区間は`observationGap`として返し、取得できなかった内容を推測しません。

## 秘匿境界

表示・保存してよい推論は、App Serverが公開した推論サマリーだけです。次はmonitor eventへ入れません。

- raw reasoning又は非公開の内部思考
- system・developer instruction、prompt全文
- toolの引数、command、無加工stdout・stderr、環境変数
- cookie、token、secret、認証情報
- 利用者homeを含む絶対path

tool eventはtool種別とlifecycleだけを表します。本文はHTMLとして解釈せずplain textで描画します。artifactはmanifestが明示するrepository内の通常fileだけを読み、path traversal、symlink、上限超過を拒否します。

## GET-only API

同じPython serverが次のread-only endpointを提供します。

| endpoint | 内容 |
| --- | --- |
| `GET /api/monitor/v1/runs?qualification=<id>` | monitor用run一覧 |
| `GET /api/monitor/v1/runs/<runId>/snapshot?qualification=<id>` | 実行・成果物・観測状態とexact ID |
| `GET /api/monitor/v1/runs/<runId>/events?qualification=<id>&after=<cursor>&limit=<n>&waitMs=<ms>` | cursor以降のevent |
| `GET /api/monitor/v1/runs/<runId>/artifacts?qualification=<id>` | manifestで宣言された保存済み成果物 |

monitor namespaceにはPOST、PUT、PATCH、DELETEを実装しません。既存のHost、session、Tailscale、same-origin、CSP、`no-store`境界を継承します。各応答の`monitorModelRequests`は常に`0`であり、endpoint処理はApp Server requestを呼びません。

## 正本と三つの状態

v2では、不変`plan.json`、小さい親`manifest.json`、`questions/<questionIdのsha256>.json`、`attempts/<token>/`の`progress.jsonl`・`result.json`・baseline、receipt、保存済みpatch、`work_versions.json`をworkflowの正本とします。`question_summary.json`、monitor event、replay、snapshotは表示のための再構築可能な観測projectionであり、工程完了、patch確定、公開可否を決めません。v1 runは既存の親・子manifestを読めますが、新規runでは生成しません。

画面とAPIは、次の状態を混ぜません。

| 状態 | 根拠 | 例 |
| --- | --- | --- |
| 実行状態 | run manifestとheartbeat | queued、running、completed、failed |
| 成果物状態 | 保存file、receipt、検証、artifact sync | 保存前、保存済み、検証済み、同期待ち |
| 観測状態 | cursor、queue、disk、接続時刻 | live、stale、gap、unavailable |

Agentが発言又はturnを完了しても、serverの保存とreceipt検証が終わるまでは「保存済み」「検証済み」と表示しません。保存前の発言と保存済み成果物は別領域に表示します。

## UI

`/monitor?qualification=<id>&runId=<id>`を独立画面として提供します。PCでは、並列lane、成果物Reader、Agent活動を同時に確認できる高密度の3ペインを基本とし、成果物Readerを最も広くします。スマートフォンでは同じ三領域をtabで切り替えます。

- laneは実eventの開始・完了時刻とexact IDから重なりを示す
- Readerは保存済みartifact本文と検証状態を表示する
- Agent活動は「Agent発言」「公開推論サマリー」「tool」「状態」「error」を明記する
- 新着が届いても読書位置を奪わず、追従中だけ末尾へ移動する
- stale、gap、再接続中を通常の実行状態と分けて表示する
- 実eventを受けた時だけpulseを出し、疑似typingや架空の活動を作らない

問題へ移動する場合は、問題整備システムの`qualification`、`listGroupId`、`questionId`を含む読取位置deep linkを使います。将来別serviceへ分離しても、API versionとstable IDを維持します。

## 検証

完成判定では、少なくとも次を自動試験とbrowser walkthroughで確認します。

- observer有効・無効で`thread/start`、`turn/start`、token usageが増えない
- monitor endpointを何度取得してもApp Server requestが0件
- 64並列相当のnotificationでproducerがblockせず、overflowやdisk失敗がrunへ伝播しない
- cursor再接続、重複除外、server instance変更、`observationGap`、stale表示
- raw reasoning、instruction、tool引数、secret、絶対home pathがevent・API・DOMへ出ない
- 保存済み成果物の本文と正本fileが一致し、未保存出力を成果物と誤表示しない
- monitor namespaceにmutation route又はAgent操作部品がない
- PCとスマートフォンで並列lane、Reader、Agent活動を確認できる
