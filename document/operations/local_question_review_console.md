# 問題整備システム

この文書は、ローカルGUIから整備・評価・再整備を実行するときの順序と安全境界の正本です。工程順とpromptは[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照してください。

## 手戻りを防ぐ運用順序

1. 実装・文書・設定の変更とテストを終え、serverを再起動する。run中は現在確定中のpatchと作業版台帳を外部から変更しない。
2. トップの年度・フォルダ（`listGroupId`）一覧で`整備・洗い替え`を開き、対象年度、整備する項目、処理する問題を指定する。工程は整備する項目から自動で決まり、serverは対象を一問queueへ分解して`00_source`と確定patchの論理projectionを次工程へ渡す。設問意図（02）は`questionIntent`だけを更新し、正答（02a）は全選択肢の`correctChoiceText`を`正しい` / `間違い`で確定する。
3. serverは一問単位の入力準備を最大64問まで同時に進め、準備できた各問題を一問一つのmodel turnへ渡す。model turnは全資格合計64本を上限に並行実行し、複数資格の実行中は資格ごとに公平配分する。modelはread-onlyで一問の候補だけを返し、serverが一問ずつ検査・確定する。不合格はqueue末尾で最大2回再実行する。
4. patch確定後は[`artifactSync`](#artifactsync)で公開用成果物を自動更新する。自動更新を完了できない場合だけ手動再生成を使う。
5. 公開用成果物が最新になった後、別sessionで評価する。合格した問題だけを明示操作でFirestoreへ反映し、readback一致を確認する。

### 操作と確認の境界

- runの開始・再開、成果物の再生成、Firestore反映など、共有状態を変更する操作は問題整備システムの画面から実行する。
- 進捗確認と整合性監査は画面表示を証跡にせず、`manifest.json`、`progress.jsonl`、`technical_log.jsonl`、問題別projection、`result.json`、完了receipt、publish runのreadbackを直接確認する。画面とartifactが食い違う場合はartifactを正として原因を調べ、完了receipt又はreadback一致がない状態を完了扱いにしない。
- 並列稼働、保存済み成果物、Agent発言、公開推論サマリーの観測には、独立した[バッチ成果物モニター](batch_artifact_monitor.md)を使う。モニターはread-onlyとし、この画面の開始・編集・公開責務を移さない。

## 確定、rollback、再生成

| 状態 | 完了条件 | 後続失敗時 |
| --- | --- | --- |
| patch確定 | 成功receipt、変更範囲、工程検証、`00_source`不変、作業版台帳を検証し、`receiptValidated=true`になった | 確定前ならrollbackする。 |
| 公開用成果物 | `artifactSync`と公開前の機械gateが成功し、現在のpatchと一致した | patchは取り消さず、再生成だけをやり直す。 |
| Firestore反映 | 現行工程版と別session評価に合格し、明示確認後のreadbackが一致した | ローカル成功だけで反映済みにしない。 |

### 整備runのfile transaction

- modelはfile、progress、receiptを変更せず、JSON Schemaに合う問題別候補だけを返す。source identityの解決、field制限、工程検査、patch・作業版・progress・receiptの保存はserverが所有する。
- serverは候補を問題別の一時workspaceへ反映して検査する。合格した一問だけを短いrepository排他内で最新patchへ反映し、失敗時はその問だけを戻す。対象を一意に解決できない問題もmodelへ渡さず、その問だけを保留する。
- patchと`work_versions.json`を同じcommit点で確定する。`receiptValidated=true`後の`artifactSync`失敗ではpatchを戻さず、成果物の再生成だけを再試行する。rollback不能又は共有状態の破損だけが親queueの停止理由である。

### 画面からの直接修正

直接修正は、対象fileのbaseline（開始前bytes）とtransaction manifestを`output/question_review_console/direct_edit_transactions/`へ先に保存してから、全fileを更新します。途中失敗では全fileを戻し、再起動時にも未完了transactionを回収します。rollback後に開始前bytesとの差分（failed delta）が残る場合は成功扱いにしません。patch保存がcommit点です。その後のcache無効化、確認記録又は再読込に失敗してもpatchを戻さず、画面へ`warning`と`postCommitErrors`を返します。本番Firestoreへは書き込みません。

### 項目を限定した洗い替え

- 人間が判断する問題整備と洗い替えは、トップの`listGroupId`一覧だけを開始地点とする。整備済みの範囲も開け、タップした範囲を初期選択したうえで他の年度又はフォルダを追加できる。資格全体の状態と工程管理画面は開始地点にしない。
- 更新項目は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の各工程の`update_targets`だけを正本とする。UI、prompt、候補schema、patch保存、作業版は同じ定義を使い、別のfield一覧を持たない。
- UIで工程を直接選ばない。選択したupdate targetが属する工程をworkflow順で自動実行する。整備できる項目は初期状態ですべて選択し、操作は`すべて選択`と`選択解除`に統一する。前提が未整備のため実行できない項目は、前提を完了するまで選択肢に出さない。
- 処理する問題は`整備が必要な問題だけ`を初期値とし、未整備、現行の整備基準を適用していない問題、要確認の問題をserverがまとめて抽出する。意図的に全件をやり直す場合だけ`選択年度の全問題を洗い替える`を選ぶ。内部状態ごとの選択肢と問題番号範囲は画面に出さない。
- 年度又はフォルダの識別には`examYear`を使わず、`listGroupId`を使う。独自問題に`examYear`がなくても実行契約は変わらない。ただし、`examYear`がある公式過去問では独自問題専用の`05_originalized`を自動的に非適用とし、`examYear`がない問題だけを05の対象にする。
- `補足質問と回答`だけを選ぶ場合、`explanationText`は候補判断の参照用であり、更新できるのは`suggestedQuestionDetailsByChoice`だけである。他の工程も、選択したupdate targetの`fields`だけを書き換えられる。
- 相互に整合させる必要があるfieldは一つのupdate targetとして選ぶ。modelが選択外fieldをset又はunsetした候補は問題単位で拒否し、patchへ反映しない。
- preview tokenとrun receiptには`selectedUpdateTargetIds`、`selectedFieldsByStage`、`readFieldsByStage`を保存する。旧runの`questionRange`は再開互換のため読み取るが、新規runの画面からは指定しない。再開時に実行条件が一つでも違う場合は別runとして確認し直す。
- APIから問題を厳密に限定する場合は`questionIds`を使う。serverは重複を除いて指定順を保持し、選択した`listGroupIds`内のinventoryにある`question.id`だけを受け入れる。未知ID、選択外年度のID、`questionRange`との併用はpreview前に拒否し、正規化した同じID集合をpreview token、plan、run receipt、再開条件へ保存する。`updateTargetIds`は更新を許可するfieldの選択であり、問題scopeには使わない。

### `artifactSync`

`artifactSync`はpatch確定後のMerge、Convert、upload-ready、upload dry-runだけを表します。

| 契機 | 自動実行 | 手動導線 |
| --- | --- | --- |
| 画面でpatchを保存 | 保存ごとに実行 | 自動更新失敗時の`パッチ変更を反映` |
| 一問queue | 全item走査後、確定した年度又はフォルダごとに1回実行 | 自動更新失敗時は管理機能の`出力` |
| 成果物が現在patchと一致 | 何もしない | 管理ツール内に非常用の強制再生成だけを残す |

完了状態は`succeeded`、`current`、`not_required`です。それ以外は更新待ちとして理由を表示します。旧工程版と混在する年度では、現行03b済みの問題だけを再生成前後で検証します。法令関連問題がすべて現行03bになった年度は、mergedとFirestore成果物の全対象を検証します。

## 問題IDと現行法監査

- `uiQuestionId`と`reviewKey`は画面表示・操作用である。03bの監査sidecarは`law-revision-audit/v2`とし、source由来の`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`の3要素が完全一致するrecordだけを結合する。`sourceRecordRef`は`00_source/`基準の相対JSON pathと0始まりのrecord indexを`<path>#<index>`で表す。
- UIの`reviewKey`が衝突しても、`sourceRecordRef`で問題を分離して資格・年度・問題一覧を表示する。3要素を一意に確定できない場合は03bだけをfail-closedでblockし、他工程の閲覧・実行は妨げない。
- selected artifactをsource recordへ対応できない場合は、path・工程・件数を`artifactResolutionBlockers`へ出し、その工程とdeliveryを完了扱いにしない。
- 技術知識や計算だけで正誤を判断できる問題は、`isLawRelated=false`、`auditStatus="not_law_related"`、`reviewState="secondary_verified"`として03b完了を記録できる。法令根拠がないという理由だけで`hold`にしない。
- 03bの工程版を記録する前に、工程03と同じ解説文の形式・日本語品質を検証する。加えて、`lawRevisionFacts`、正答対応、verified根拠、v2 sidecarの識別・分類・必須metadataをserverが検証する。どちらかに失敗した成功receiptは確定しない。

判断内容と保存項目は[現行法監査](current_law_question_maintenance_workflow.md)と[03b prompt](../../prompt/03b_prompt_audit_current_law_and_patch.md)を正本とします。

## 一問queueとsession

```text
browser -> Python server -> Codex App Server（stdio）
```

Python serverはChatGPT app同梱の`codex app-server`を一つ管理します。PATH上の別binary、`codex exec`、OpenAI Platform API、外部model providerへfallbackしません。初回は`gpt-5.5`、候補生成又は機械検査に失敗した問題の再試行は`gpt-5.6-sol`を使い、推論強度はどちらも`high`とします。成功した問題は再投入せず、再開時も失敗した問題だけに直前の検査feedbackを引き継ぎます。要求modelと返された実modelはattemptとmanifestへ保存します。評価、再整備、再評価は`gpt-5.5`、推論強度`high`をturnごとに指定します。

- GUIでは資格、年度又はフォルダ、整備する項目、処理する問題を指定し、serverが`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`、工程、update targetの組へ分解する。一問だけ残る場合も同じqueueを使う。資格全体で一つだけ持つ方針・03c分類は問題patchではなく共有前提として分離し、失敗時は依存する問題工程だけを保留する。
- serverは問題の現在projectionをrunごとの希望上限まで同時に準備し、一問を一つの独立したmodel turnへ渡す。1資格の希望上限は64問・64本、全資格で同時に実行するtop-level model turnは合計64本までとする。UIではrunごとの希望上限を1、5、10、32、64から選べ、初期値は64とする。1資格だけなら最大64問を64本で同時に整備し、2資格なら原則32本ずつ、3資格なら22、21、21本のように公平配分する。資格の開始・終了に応じて新しく取得するslotから動的に再配分し、既に実行中のturnは途中で止めない。provider失敗時はrun内のadaptive schedulerが次の再試行roundの並列数を自動で縮小する。`hooks/list`、`thread/start`、`mcpServerStatus/list`、`turn/start`の短いcontrol-plane RPCも全資格合計64本まで受け付ける。実model turnと別のbudgetとして観測するが、自己都合の8本制限で起動を8waveへ直列化しない。さらに、直前waveのserver writer待ちを保持するpipeline枠を最大64問分確保する。正本patchの同時writer数は増やさず、writer待ちが次waveのmodel turnを占有しない構造にする。
- modelは一問の構造化候補を返すだけで、検査commandや成功receiptを自己申告しない。serverは候補ごとにsource identity、許可field、工程品質、`00_source`不変を検査し、合格recordだけを確定patchへ反映する。他問題の不合格や曖昧さは波及しない。
- 第01工程は、全問題に対して同じsource snapshotを使う独立したread-onlyレビューを2回実行し、serverが結果を照合してから通常の問題形式候補を生成する。レビューの詳細schemaはproductionコードを正本とし、この文書には複製しない。予約、二つの結果、照合結果は、親manifest全体へ書き戻さず、親run配下の`aggregate_review_checkpoints/<questionIdのsha256>.json`へ問題単位で保存する。異なる問題の記録は互いのlockを待たず、同じ問題のslotだけを直列化する。二者不一致、source hash不一致、判定不能又は境界不明は問題単位の`hold`とし、patchへ反映しない。対象確定時の記述本文はserverが合意済みspanから切り出し、model出力の文章を保存しない。
- 初期対象外の先行工程はitemを作らず、その問で最初に必要な工程から始める。writerが確定したpatchは、物理Mergeを挟まず共通projectionで次工程へ渡す。patchが実際に変わった時だけ初期対象外の後続を再判定し、準備後の手動変更も最新入力で再準備する。一問の失敗は理由付き`blocked`とし、その問の依存後続だけを保留する。対象外は`not_applicable`で閉じ、他問を止めない。
- 正本文書又は工程版がrun中に変わった場合は、その問題だけを最新projectionでqueueへ戻す。通常対象を先に終え、不合格問題はfeedback付きでqueue末尾へ回す。品質検査は初回を含む3回で打ち切る。
- 一問を安全に破棄又はrollbackできる失敗は他問へ波及させない。候補内容、provider又はschemaの失敗は、その一問だけをqueue末尾へ戻す。provider障害が同時に発生した場合は次の再試行roundの並列数を縮小し、回復しなければ`interrupted`として再開を待つ。
- 通常の一問turnは子runのmanifestを作らない。run開始時の完全な対象・工程・field契約は変更しない`plan.json`へ保存し、親`manifest.json`はrun全体の状態、heartbeat、集計値だけを持つ。一問の可変状態は`questions/<questionIdのsha256>.json`を正本とし、工程状態、`validationAttempts`、attempt metadata、検証済み作業版receiptを同じ一問内に保存する。attempt IDにも同じ完全SHA-256を含め、全plan又は全問題を走査せず対象JSONへ直接到達する。不変planはfile identityが変わった時だけ再読・hash検証する。model結果、`progress.jsonl`、開始前baselineは`attempts/<token>/`へ置く。通常のpatch形式は従来どおり工程・年度単位を維持し、patch fileを一問ごとには分割しない。
- `question_summary.json`は一問stateから再生成できる表示用の派生物であり、正本ではない。64問の`preparing`又は確定結果はcoordinatorがまとめて更新し、各turnの`prepared`と単一writer内の`committing`は該当する一問JSONだけへ保存する。これにより、異なる問題のfile I/Oをglobal lockへ集約せず、親manifestを一問ごとに書き直さない。共有前提だけは資格全体のwriterとして独立runを持てる。
- 一問writerはpatchと`work_versions.json`の開始前bytesを同じbaselineへ保存してからtransactionを開く。検査、patch更新、作業版更新又はcheckpoint保存のどこで失敗しても両方を開始前へ戻し、確定済みattemptは以後変更しない。途中再起動ではtransactionが開いた一問だけをrollbackし、確定済みreceiptを持つ一問は維持し、未確定の問だけをqueueへ戻す。工程の方針fingerprintが欠けるitemは確定済みとみなさず再検査する。rollback又は残存差分を確認できないrunは再開せず、成果物同期もしない。
- 物理Merge、Convert、upload-ready、upload dry-runはqueue終了時に確定したlistGroupIdごと1回だけ実行する。失敗してもpatchは保持し、更新待ちのときだけ手動再生成を表示する。

評価と再評価は問題ごとの新しいread-only thread、再整備は問題ごとの新しいworkspace-write threadで実行し、異なる作業でthreadを再開・forkしません。開始前にChatGPT認証、利用上限、公式provider、`Standard` service tier、追加Codex creditsが無効であることを確認します。同じwaveの各turnは、直前60秒以内に得た一つの検証済み利用資格を共有します。cacheがない場合又は期限を過ぎた場合は、最初のturnだけが再取得し、同時に来た後続turnはその結果を待ちます。問題整備は`Standard`だけを使用し、UI又はAPIから`Fast`を指定しても開始しません。追加Codex creditsが有効な場合もfail-closedで停止します。model、推論強度、read-only候補生成、一問ごとの機械検査、writer制限は変えません。API key、従量課金plan、外部MCP・plugin・app・hook・browser操作は使いません。調査と保存はどちらも`multi_agent=false`の単一threadで実行し、調査だけを隔離したread-only threadと組み込みweb検索に限定します。hook無効化とMCP無効化は各threadで検査し続ける。64個の独立threadが同時に通信できるよう、長寿命Codex App Serverを起動する直前にprocessのfile descriptor soft limitを65,536以上へ引き上げる。hard limitが不足する、又は引上げを確認できない場合はrun開始前に停止する。

## 作業バージョン

工程版は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の`policy_version`だけを`MAJOR.MINOR`形式で管理します。洗い替え不要の改訂はMINOR、必要な改訂はMAJORを上げます。公開済みだが使用版を証明できない初期値は`v0.0`です。

run開始時とreceipt検証時に、完全な版番号と正本文書fingerprintを照合します。全更新項目を実行した場合は工程単位、部分実行ではupdate target単位で、成功receiptを検証した対象だけを`work_versions.json`へ記録します。未選択のupdate targetは現行版になりません。`stateHash`変更又は現行MAJOR未満は再整備、評価版のMAJOR変更は再評価の対象です。

## 進捗、heartbeat、技術ログ

- `progress.jsonl`は、問題ごとに`question_started`、`policyTargets`順の`stage_completed`、`question_completed`を直後に追記する。`policyTargets`には現在runの正式な問題IDだけを保存し、aliasや旧runのIDを補完しない。順序違反、重複、対象外工程は無効であり、完了数へ含めない。
- `processed`は全イベントがそろった状態、`validated`は成功receiptをserverが確認した状態である。停止時のprocessed出力は`未承認`とし、完了表示や作業版記録に使わない。親runは必要な全工程がvalidatedになった問題だけを完了とする。
- 問題projectionの準備中も15秒間隔で`heartbeatAt`と`preparationProgress`を更新する。準備は64問単位で区切り、同じ区切りの一問入力を独立workerで同時に作る。入力準備はmodel・writerのpipeline workerと分離し、実行中model 64問とwriter待ち最大64問を上限として未完了futureを保持する。前waveのwriter待ちが次の64入力生成を塞がず、全対象分の入力とfutureを一度にメモリへ保持しない。対象解決用patch JSONはpathと内容fingerprintで再利用し、正本が更新された時だけ読み直す。準備できた各問から独立したmodel turnへ逐次投入するため、全問の準備完了を待たない。model候補はread-onlyである。一問stateと集約回答checkpointは問題ごとのlockで並行し、親queueの集計更新と正本patchの検査・確定だけを必要な範囲で直列化する。
- App Serverの状態は、64枠への入場待ちを含む`turnBudget`、起動RPCの64枠を示す`controlPlaneBudget`、`turn/start`完了後からturn終了までを数える`modelTurns`に分けて返す。64問同時実行の証明には`modelTurns.peakInFlight=64`を使い、予約だけで実行済みと判断しない。
- Codex App Serverのturn待機中も15秒間隔で`heartbeatAt`を更新する。親runのheartbeat writerはcoordinator一つだけとし、各一問turnから親manifestへheartbeatを書き込まない。heartbeatはjobの`lastActivityAt`へ伝播するが、問題処理又はreceipt検証の完了を意味しない。
- 一つのmodel turnが15分で完了しない場合は中断し、その一問だけを失敗としてqueueの再試行契約へ戻す。
- runごとの`technical_log.jsonl`はappend-onlyで、`sequence`、`observedAt`、`level`、`message`を保存する。該当時は`commandStatus`、`exitCode`、`outputTail`、repository相対`changedPaths`も保存する。同一イベントを重複記録せず、秘密情報と思考過程を除く。
- 通常のrun・job APIは要約だけを返す。技術ログは`GET /api/qualification-runs/<runId>/technical-log?qualification=<qualification>`から、画面で展開中だけ取得する。
- Run進捗の通常取得は一問stateを展開せず、派生summaryだけを返す。問題一覧が必要な場合だけ`includeQuestions=true`を指定し、さらに一問のattemptと工程履歴が必要な場合は`GET /api/qualification-runs/<runId>/questions/<questionId>?qualification=<qualification>`で対象JSONだけを読む。`GET /api/session`はApp Serverへの同期問い合わせを行わず、最後に検証済みの接続状態だけを返す。

- 画面は一つのpoll管理でrun、job、進捗を更新し、実行dialog表示中は背景pollを止めます。進行中runの背景pollは軽量な進捗だけを取得し、履歴一覧はrun終了時、待機中又は明示更新時に取得します。詳細表示中の問題は定期再取得せず、一覧で確定した同じsnapshotを使い、画面へ戻った時又は明示更新時に差分を確認します。問題は分野・問題番号とsource上の自然な順序で表示し、processedとvalidatedを分けます。進捗から問題を開く「作業対象を確認」には、問題文・選択肢・正答・解説とpatch適用後の`questionType`、問題整備専用の`isCalculationQuestion`を表示します。`flash_card`と`group_choice`の基本解説は問題共通の1本として選択肢一覧の上に表示し、選択肢カードへ繰り返しません。問題の詳細画面では、選択肢をタップすると、その選択肢の`suggestedQuestionDetails`に相当する質問と回答だけをカード内に表示します。`suggestedQuestionDetailsByChoice`が0件の選択肢も、保存済み補足がないことを明示します。補足0件は不備ではなく、基本解説と重複する候補を保存しない正規状態です。旧flat fieldしかない場合は「選択肢未割当・再生成が必要」と表示し、推測で割り当てません。
- `05_originalized`が適用された独自問題の詳細画面では、`00_source`の問題文・全選択肢と、05以降の確定patchを適用した現在の問題文・全選択肢を読取専用で並べます。問題文と選択肢それぞれについて完全一致か変更ありかを表示し、選択肢が`00_source`と同一でも正常な独自問題として確認できるようにします。公式過去問など05未適用の問題には、この比較を表示しません。
- トップの初回表示、資格切替、再読込では、取得中・画面反映中を区別できる待機パネルを表示します。待機パネルには接続確認、整備状況、実行中の作業、必要な場合は問題一覧、画面反映の段階と経過時間を示します。10秒を超えた場合も処理が続いていることを明示し、取得失敗時は再試行できる状態にします。読込完了後のトップは資格選択と年度・フォルダカード一覧だけを表示し、資格全体の基準、必要問題数、作業状況、進捗見出しの独立カードは置きません。法令工程の資格設定は`整備・洗い替え`の開始dialogへ置きます。実行中の作業は対象カードの表示と`進捗を見る`ボタンへ集約します。整備状況、実行中の作業、表示中の問題一覧は並行して取得します。問題一覧の絞り込み、状況確認の初回取得、実行対象のpreviewにも、それぞれ操作箇所に近いローディングを表示します。
- PCではトップと問題一覧の内容幅に上限を設け、横長画面でも情報のまとまりと読みやすい行長を保ちます。トップの資格選択はフォルダ一覧の中央へ配置し、左右どちらかへ寄せません。スマホでは画面幅を使い切る従来の一列表示を維持します。
- 問題文の条件から式と計算で答えを一意に求められる公式過去問は`flash_card`として整備します。状況確認画面では、公開用データの各選択肢を別々のカードにし、正誤を対応する選択肢へ表示した上で、計算過程をまとめた基本解説1本を選択肢一覧の上に表示します。
- トップの各年度・フォルダには、`整備・洗い替え`の左に読取専用の`問題一覧を見る`を置きます。進捗ゲージは全工程の作業項目を分母にして途中工程の完了も反映し、`整備済み`の問題数は必要な全工程が現行基準になった問題だけを数えます。
- トップの初期取得は、資格・フォルダ構成、フォルダ別の整備要約、直近Runの一覧要約だけに限定します。問題本文、選択肢、公開用document、`questionExecutions`、対象record全件などの詳細は、問題一覧又はRun詳細を開いた時だけ取得します。フォルダ別の整備要約はsource・patch・生成物のfingerprintと対応する小さな派生snapshotを`output/question_review_console/cache/workflow_groups/`へ保存し、fingerprintが一致する間は全問題projectionを組み直しません。Run一覧も各Runの`list_summary.json`をmanifest保存時に更新し、一覧表示では巨大な`manifest.json`を解析しません。snapshot又はsidecarが欠損・不一致なら正本から再生成し、派生cacheの保存失敗だけで整備処理を失敗させません。
- トップの取得中表示は、進捗バー、その近くの現在状態、経過秒数だけにします。工程ごとの丸印、spinner、重複した説明文は表示しません。同じ資格の前回のフォルダ要約は同一originの`localStorage`へ最長6時間保持し、再読込時は前回表示を直ちに復元してから最新要約へ差し替えます。cache表示中も資格選択と`問題一覧を見る`は操作できます。`整備・洗い替え`は、表示中の資格についてRun状態を確認済みで、稼働中Runがなく、workflow再起動も不要な場合だけ操作できます。取得中に資格を切り替えた場合は以前の取得を中止し、遅れて完了した以前の資格の応答を現在画面へ反映しません。最新取得に失敗しても前回表示を消しません。
- トップの資格・フォルダ要約は、UIサーバー内で全問題を同期集計しません。`tools/question_review_console/workflow_overview_builder.py`を別Pythonプロセスとして実行し、資格ごとの完成済みread modelを`output/question_review_console/cache/workflow_overviews/<qualification>.json`へatomicに保存します。UIサーバーは最後に完成したread modelを直ちに返し、同じ資格の再集計は同時に1本だけ裏で実行します。整備Run中は正本更新による無効化だけを記録し、重い再集計はRun終了後に一度だけ行います。実行履歴も`output/question_review_console/workflow_runs/<qualification>/dashboard_runs.json`の資格単位索引から読み、通常表示のたびに全manifestを走査しません。これらは表示専用の派生cacheであり、問題、patch、run manifest、工程バージョンの正本にはしません。cacheがない初回又は壊れている場合だけ正本から再構築します。
- `問題一覧を見る`を開くと、最初の画面にはタップした`listGroupId`の全問題一覧だけを表示します。一覧画面の初期状態も`全問`へ統一します。初回取得中の待機表示は一覧領域の中央一か所だけに置き、上部帯と集計欄にはspinnerを重複表示しません。一覧画面の表示状態、URLの`view=questions`、対象資格・`listGroupId`、一覧取得処理を一つの状態として扱います。Safariによるページ復元、再読込、非表示からの復帰で一覧画面が表示されている場合は、同じ入口から未取得データを読み込み、表示だけが残って取得処理が始まらない状態を作りません。既に取得した通常一覧は同一originの`localStorage`へ最長6時間保持し、再読込時は前回表示を直ちに復元してから最新状態へ差し替えます。最新取得が一時失敗した場合も前回表示を消さず、更新に失敗したことを一覧の近くへ表示します。検索又は絞り込み結果はcacheへ保存しません。
- 一覧APIは問題詳細を要求のたびに全件装飾しません。問題番号、問題文、正誤・解説の要約、最終更新日、一覧用filter fieldだけを別Pythonプロセスで資格単位のread modelへ集計し、`output/question_review_console/cache/question_lists/<qualification>.json`へatomicに保存します。UIサーバーは完成済みread modelをメモリ上で検索・並べ替え・ページングします。整備Run中はread modelの無効化だけを記録し、再集計はRun終了後に一度だけ行います。完成済みcacheがない初回もHTTP要求内で全件集計せず、中央の待機表示を保ったまま独立プロセスの完成を短いAPI pollingで待ちます。表示後に全件状態を再集計する二本目のAPIは実行しません。
- 問題をタップしたときはURLへ`questionId`を保持して詳細画面へ移り、一覧で取得済みの問題番号と問題文を直ちに表示してから、正答、解説、補足質問などの読取専用内容を取得します。読取専用内容は年度・フォルダ単位の別Pythonプロセスで作り、`output/question_review_console/cache/question_details/<qualification>--<listGroupId>.json`へatomicに保存します。HTTP要求では完成済みread modelから対象一問だけを返し、同一フォルダ内の二問目以降はprojectionを再構築しません。工程バージョン、レビュー履歴、失敗run差分、評価、Firestore比較は読取専用画面の表示条件にせず、将来の管理画面又は明示的な管理操作を開いた時だけ取得します。整備Run中に完成済みread modelがない場合は、同じserver内のinventory snapshotから表示内容だけを取得し、途中成果物を別プロセスで集計しません。
- 同じ一覧状態の詳細は画面内cacheを再利用し、一覧read modelの版が変わった時だけ再取得します。詳細の更新確認もread modelの`detailVersion`だけを読み、装飾済み詳細を定期再計算しません。詳細画面から戻ると検索位置を保った問題一覧へ戻り、一覧取得時に先頭問題を自動表示しません。詳細の取得中は以前の問題を消し、選択した問題の概要とローディングだけを表示して、別の問題に対する操作を誤って実行できない状態にします。問題一覧では、問題検索と、`00_source`と正答が異なる問題、問題文から選択肢を取得した問題、計算問題、法令問題の絞り込みを利用でき、条件は併用できます。この画面では人間監査結果を保存せず、編集、評価、Firestore反映の操作を主画面へ出しません。
- 年度・フォルダカード、問題一覧の各行、問題詳細の見出しには、問題内容の`最終更新`を年月日・時刻で表示します。この日時はFirestoreの`updatedAt`ではなく、現在の問題projectionを構成する`00_source`と適用済みpatchのファイル更新日時のうち最新の値です。年度・フォルダカードは、配下の全問題の`最終更新`の最大値を表示し、日時を確定できない場合は`最終更新 —`と表示します。カードの並びは年度・フォルダの自然順を維持します。問題一覧は最終更新が新しい順を初期値とし、画面から古い順へ切り替えられます。ページング前に全対象をこの順序へ並べるため、追加読込後も順序は変わりません。閲覧又は読取専用の評価だけでは更新せず、source又はpatchが実際に保存された時だけ変わります。
- 「00_sourceと正答が異なる」は、`00_source.correctChoiceText`とpatch適用後の`correctChoiceText`を問題単位で比較して絞り込みます。`○`と`正しい`、`×`と`間違い`は同じ判定として扱い、実質的に変わった選択肢だけを詳細画面へ表示します。この差分は確認対象であり、それだけで整備失敗又は公開不可とは判定しません。問題文から選択肢を取得した問題は、有効な`aggregateAnswerDecomposition`が`target/approve`であり、現在の`questionBodyText`に対するsource hashとspan検証を通る場合だけ該当します。計算問題はpatch適用後の`isCalculationQuestion: true`、法令問題は`isLawRelated: true`を使って絞り込みます。

## 検査feedbackと改善記録

各工程は通常queueを一巡してから、不合格問題だけをserverの検査feedback付きで最大2回再整備します。各attemptの指摘と結果は`validationAttempts`と技術ログへ保存し、次の候補生成には該当問題のfeedbackだけを渡します。

queueがterminalになった後、`improvement_report.json`へ工程・指摘code・fieldごとの発生問数とattempt数を集計します。3問以上で同じ指摘が出た場合、又はモデル側の検査は通ったのにserverが拒否した場合を改善候補とします。正本文書、prompt、checker、testの変更はactive run中に行わず、別の改善jobで候補を確認して実施します。checkerを変える場合は、該当工程の正本・検査契約と[`policy_version`](../../config/question_maintenance_workflow.toml)を同時に更新します。既存問題の洗い替えが必要ならMAJOR、今後の作業だけに適用できる変更ならMINORを上げます。

## 評価と公開の安全境界

- 評価は問題文と全選択肢を一問ずつ独立に判定する。現在の正答は先に渡さず、serverが全肢の結果、正答対応、解説品質、重大指摘を検証する。
- 非法令問題の解説本文に機関名、資料名、URLがないことだけを減点理由にしない。根拠不足は`insufficient_evidence`として不合格にする。
- `questionBodyText`と`choiceTextList`は自動整備せず、blind reviewを伴う`24_questionIssueCorrections`で扱う。
- Firestore反映はCodex threadへ任せず、preflight、UIの明示確認、直後のreadbackを使う。

## 起動

```bash
.venv/bin/python tools/question_bank/question_bank.py review-ui
```

serverは`127.0.0.1`だけへbindします。本人端末から使う場合だけTailscale Serveのprivate HTTPSを使います。

serverはTCP listenerを確保した後、process全体のfile leaseを取得してから中断回収を始めます。二つ目のserver processはrunを変更せず起動に失敗し、同じ資格のrun writerも資格単位leaseで一つに限定します。`QualificationRunStore`の生成だけでは回収又はfile更新を行いません。

起動時の中断回収は、`workflow_runs/*/*/recovery.json`に記録された実行中runだけを読みます。過去の全manifestを毎回解析しません。回収対象sidecarはrunを実行状態へ保存する前に作成し、terminal状態をmanifestへ保存した後に削除します。初回索引作成時に壊れた過去manifestがあっても、実行中の正常なrunの回収を妨げません。旧形式から初回移行したことは`workflow_runs/.recovery-index-v1.json`へ記録します。
