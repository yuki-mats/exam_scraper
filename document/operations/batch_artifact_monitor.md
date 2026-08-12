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

観測eventのdisk logもmonitor専用の固定上限内でローテーションします。各runは4 MiBの現行logと一つのbackup、16 KiB以下のsnapshotに限定し、全体では新しい64 runだけを保持します。projection lockの外にbounded disk queueを置き、repository全体の短時間・non-blocking file lockでrun作成、rotation、retentionを直列化します。lock競合、queue overflow、disk失敗はAPIやrunを待たせず観測状態を`degraded`にします。fileとdirectoryは`dir_fd`、`O_NOFOLLOW`、inode、single-link通常fileを検証し、部分writeは元sizeへrollbackします。monitorの保存量を無制限に増やしてworkflow artifactと同じfilesystemを圧迫しません。

disk queueでは、未取得の`agentMessage`と`reasoningSummary`の累積置換だけをroute・thread・turn・item・summary index単位でまとめます。memory replayとUI eventは全件を維持し、pendingは各stream最大1件、全体512件、256受理sequence以内です。上限圧力時は最古streamから確定します。非stream event、terminal、error、tool、thread、token、turn、gapの受理前にもpendingを確定し、順序とdurabilityを保ちます。JSONLには最新全文と`diskCoalescing`の`coalescedCount`、受理sequence範囲、観測時刻範囲を保存します。disk telemetryはtype別集約数、pending数、上限、最大sequence滞留、flush数を公開します。

queue overflowやApp Server接続の再作成など、notificationの連続性を証明できない境界は`observationGap`として残します。gapの対象runは欠落を検出した瞬間のimmutable route snapshotで固定し、後から開始したrunへ付け替えません。同じparentに属する多数のchildへは一つのgap eventをindexし、diskにはparent runへ一度だけ保存します。

pending gap、runtime binding、active routeにも固定上限を設けます。上限を超えた相関は、欠落件数を失わない`scopeTruncated: true`のglobal gapへ集約し、対象scopeの連続性を正常とは扱いません。workerへ受理済みのeventは`drain()`又は`close()`が既定5秒以内にprojectionとdisk保存を完了した時だけ完了扱いにします。timeout又はworker停止は明示的な例外とし、失敗を成功として隠しません。timeout後もaccepted itemを捨てず、`close()`を再試行できます。

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
  "occurredAt": 0,
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

`observedAt`はPython serverがnotificationを観測した時刻です。App Serverがevent自身の開始・完了時刻を返した場合だけ、その値を`occurredAt`へ保持し、laneの時間関係には`occurredAt`を優先します。thread-level eventへ現在の`turnId`を補うなど、存在しない相関を推測しません。

公開するeventは、Agent発言、公開推論サマリー、turn lifecycle、tool名と状態、token usageなど、表示に必要なallowlist対象だけです。deltaは同じ`itemId`の受信済み部分をserver側で結合・秘匿した累積置換previewとして返し、browserが断片からsecretを復元できないようにします。previewは4,096文字で終了します。modelを使った要約、分類、関連付け、補完は行いません。

cursorは`serverInstanceId`と`sequence`から作ります。browserは最後に確認したcursor以降を再取得します。memory上のreplay範囲外、queue overflow、server再起動などで連続性を証明できない区間は`observationGap`として返し、取得できなかった内容を推測しません。

## 秘匿境界

表示・保存してよい推論は、App Serverが公開した推論サマリーだけです。次はmonitor eventへ入れません。

- raw reasoning又は非公開の内部思考
- system・developer instruction、prompt全文
- toolの引数、command、無加工stdout・stderr、環境変数
- cookie、token、secret、認証情報
- 利用者homeを含む絶対path

tool eventはtool種別とlifecycleだけを表します。本文はHTMLとして解釈せずplain textで描画します。`Authorization`、`Proxy-Authorization`、cookie、private keyなどは改行単位でfail-closedに秘匿します。artifactはmanifestが明示するrepository内のsingle-link通常fileだけを読み、path traversal、symlink、hardlink、読取中変更、上限超過を拒否します。

問題別recordを共有JSONから取り出す場合は、manifestが宣言した`sourceQuestionKey`、`sourceRecordRef`、`reviewQuestionId`のうち存在する全fieldがrecord側にも存在し、完全一致する一件だけを採用します。問題別結果があるrunの共有JSONを問題へ帰属できない場合は、黙って省略せず`question_attribution_required`として拒否します。欠落、競合、複数一致はfail-closedで拒否します。artifact APIの総量上限は、重複宣言を展開した後のHTTP JSON payload全体のUTF-8 byte数へ適用し、宣言数と応答byte数の切詰めを応答に明記します。

run manifest、`list_summary.json`、dashboard indexも、検査後にpathから開き直しません。run storeを起点に`dir_fd`と`O_NOFOLLOW`でたどり、開いたdescriptorの通常file・single-link・inode・size・mtimeを読取前後で確認します。path上の`qualification`・`runId`・親子関係とmanifest内部のidentityが一致しない場合は表示へ混ぜません。

一回のsnapshotは最新128 child、artifact探索は最大512 child、child manifestの合計読取量は16 MiBまでです。dashboard indexは8 MiB、indexがない場合のdirectory scanは4,096 entryまでに制限します。上限、巨大child、identity不一致による欠落は`truncated`、`warnings`又は`rejected.reasonCode`に明記し、空の正常結果に見せません。共有JSONから抽出した一recordも整形後に1 MiBを超える場合は、不完全なJSONとして切り詰めず拒否します。

run一覧、snapshot、events、artifactの各HTTP JSON payloadには4 MiBの総量上限を適用します。上限を超えるrun一覧やsnapshotは、最新laneを優先して保持し、`truncated`と警告を返します。eventsは返却できた最後のeventのcursorを返すため、次のGETで欠落なく続きを取得できます。event readerがiterableを返す場合も`limit + 1`件を超えてmaterializeしません。

`question-maintenance-run/v2`では、親manifestだけでidentityを確定せず、次の順に検証してからlaneと成果物を投影します。

1. 親manifestが所有する`plan.json`をsingle-link通常fileとして読み、`planHash`をcanonical JSONから再計算する
2. `question_summary.json`の全question・stage identityがimmutable planと完全に一致することを確認する
3. `questions/<sha256(trim(questionId))>.json`の`planHash`、`questionId`、`selfHash`を確認する
4. terminal stageはvalidated attempt、所有path上の`result.json`、receipt、問題別`batchQuestionResults`、`changedFiles`、output fingerprintを相互照合する
5. 共有JSONのrecord bindingにはplanとsummaryで確定したidentityだけを使い、state側だけに存在するidentityを採用しない

plan、summary、state、receiptのいずれかが欠落・改変・別runへのsymlink・identity不一致ならfail-closedで除外し、warning又はrejectionを返します。v2 planは16 MiB、state探索は最大1,024問、attempt投影は最大512件です。planはimmutableであってもsnapshotごとにsecure readとhash再計算を行い、改変直後に古いcacheを正常な投影として返しません。

## GET-only API

同じPython serverが次のread-only endpointを提供します。

| endpoint | 内容 |
| --- | --- |
| `GET /api/monitor/v1/runs?qualification=<id>` | monitor用run一覧 |
| `GET /api/monitor/v1/runs/<runId>/snapshot?qualification=<id>` | 実行・成果物・観測状態、exact ID、成果物fingerprintの完全性 |
| `GET /api/monitor/v1/runs/<runId>/events?qualification=<id>&after=<cursor>&limit=<n>&waitMs=<ms>` | cursor以降のevent |
| `GET /api/monitor/v1/runs/<runId>/artifacts?qualification=<id>&after=<cursor>&limit=<1..64>` | manifestで宣言された保存済み成果物。`limit`指定・cursor未指定は先頭page |

monitor namespaceにはPOST、PUT、PATCH、DELETEを実装しません。既存のHost、session、Tailscale、same-origin、CSP、`no-store`境界を継承します。各応答の`monitorModelRequests`は常に`0`であり、endpoint処理はApp Server requestを呼びません。

GET処理はworkflow正本だけでなくdashboard cache、list summary、receiptも生成・更新・reconcileしません。既存のdashboard indexがない場合はmanifestをbounded read-only scanし、monitor表示のための派生fileを書きません。

artifact cursorは、同じqualification・run・検証済み宣言collection内で「次に未処理の宣言」を示します。一pageは最大64宣言・64file・4 MiBです。4 MiB境界で入らない宣言は消費せず、`nextCursor`から次pageで読み直します。一宣言だけでJSON応答上限を超える場合は、その宣言を`response_item_bytes_limit`として明示的に拒否してcursorを前進させ、同じcursorで永久に停止しません。collectionがpage取得中に変化した場合は`resetRequired: true`と新しい先頭pageを返します。

UIは全pageを自動結合せず、「前へ」「次へ」でpage単位に置き換えます。これによりbrowserが最大件数分の本文を保持せず、v2 state/receipt走査も表示中pageの更新時だけ行います。2,048宣言、512 attemptなど全体探索上限を超えた欠落はpage継続とは別の恒久的な`truncated`・warningとして明示します。`after`と`limit`を指定しない既存clientには、従来どおり一つのbounded応答を返します。

## 正本と三つの状態

v2では、不変`plan.json`、小さい親`manifest.json`、`questions/<questionIdのsha256>.json`、`attempts/<token>/`の`progress.jsonl`・`result.json`・baseline、receipt、保存済みpatch、一問単位の`work_versions/<reviewKey hash>.json`をworkflowの正本とします。`question_summary.json`、monitor event、replay、snapshotは表示のための再構築可能な観測projectionであり、工程完了、patch確定、公開可否を決めません。v1 runは既存の親・子manifestを読めますが、新規runでは生成しません。

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
- manifestが示すterminal状態と完了時刻を、古い開始eventやtool完了eventでrunningへ戻さない
- Readerは保存済みartifact本文と検証状態を表示する
- child成果物ではchildとparentの検証・同期失敗をどちらも隠さない
- Agent活動は「Agent発言」「公開推論サマリー」「tool」「状態」「error」を明記する
- run選択肢は日時とstable ID末尾を併記し、同時刻帯のrunも一意に識別できるようにする
- 新着が届いても読書位置を奪わず、追従中だけ末尾へ移動する
- stale、gap、再接続中を通常の実行状態と分けて表示する
- eventが0件のrunは「観測live」とせず、実行中ならevent待ち、終了済みなら観測eventなしと明記する
- snapshotは定期取得するが、重いartifact本文は初回とartifact fingerprintの変化時だけ再取得する
- `artifactFingerprint`はartifact宣言、receipt・sync・revision・hashと、安全に検証できたfileのinode・size・mtime・ctime署名から作るopaque値とし、通常のlane進行状態を含めない
- `artifactFingerprintComplete`は、snapshotが全terminal outputのvalidated attemptとfile署名を含む時だけ`true`にする。v2の表示上限などで`false`の場合は、fingerprintが変わらなくても15秒ごとにartifact APIを再照合する
- v2 summaryでfingerprintが欠落した旧terminal stageも状態遷移sentinelへ含め、正常扱いせずwarningを表示する
- 終了済みrunに非terminal laneが残る場合はwarningを出し、UIで稼働中の強調を行わない
- snapshot又はartifactの更新失敗、取得拒否、payload切詰めを古い表示のまま隠さない
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
