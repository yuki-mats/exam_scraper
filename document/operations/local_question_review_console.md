# 問題整備システム

この文書は、ローカルGUIから整備・評価・再整備を実行するときの順序と安全境界の正本です。工程順とpromptは[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照してください。

## 手戻りを防ぐ運用順序

1. 実装・文書・設定の変更とテストを終え、serverを再起動する。run中は現在確定中のpatchと作業版台帳を外部から変更しない。
2. トップの年度・フォルダ（`listGroupId`）一覧で`整備・洗い替え`を開き、対象年度、整備する項目、処理する問題を指定する。工程は整備する項目から自動で決まり、serverは対象を一問queueへ分解して`00_source`と確定patchの論理projectionを次工程へ渡す。設問意図（02）は`questionIntent`だけを更新し、正答（02a）は全選択肢の`correctChoiceText`を`正しい` / `間違い`で確定する。
3. serverは入力token量で複数問をまとめ、複数のmodel turnを自動並列化する。modelはread-onlyで候補だけを返し、serverが一問ずつ検査・確定する。不合格はqueue末尾で最大2回再実行する。
4. patch確定後は[`artifactSync`](#artifactsync)で公開用成果物を自動更新する。自動更新を完了できない場合だけ手動再生成を使う。
5. 公開用成果物が最新になった後、別sessionで評価する。合格した問題だけを明示操作でFirestoreへ反映し、readback一致を確認する。

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

直接修正は、対象fileのbaseline（開始前bytes）とtransaction manifestを`output/question_review_console/direct_edit_transactions/`へ先に保存してから、全fileを更新します。途中失敗では全fileを戻し、再起動時にも未完了transactionを回収します。rollback後に開始前bytesとの差分（failed delta）が残る場合は成功扱いにしません。

patch保存がcommit点です。その後のcache無効化、確認記録又は再読込に失敗してもpatchを戻さず、画面へ`warning`と`postCommitErrors`を返します。本番Firestoreへは書き込みません。

### 項目を限定した洗い替え

- 人間が判断する問題整備と洗い替えは、トップの`listGroupId`一覧だけを開始地点とする。整備済みの範囲も開け、タップした範囲を初期選択したうえで他の年度又はフォルダを追加できる。資格全体の状態と工程管理画面は開始地点にしない。
- 更新項目は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の各工程の`update_targets`だけを正本とする。UI、prompt、候補schema、patch保存、作業版は同じ定義を使い、別のfield一覧を持たない。
- UIで工程を直接選ばない。選択したupdate targetが属する工程をworkflow順で自動実行する。整備できる項目は初期状態ですべて選択し、操作は`すべて選択`と`選択解除`に統一する。前提が未整備のため実行できない項目は、前提を完了するまで選択肢に出さない。
- 処理する問題は`整備が必要な問題だけ`を初期値とし、未整備、現行の整備基準を適用していない問題、要確認の問題をserverがまとめて抽出する。意図的に全件をやり直す場合だけ`選択年度の全問題を洗い替える`を選ぶ。内部状態ごとの選択肢と問題番号範囲は画面に出さない。
- 年度又はフォルダの識別には`examYear`を使わず、`listGroupId`を使う。独自問題に`examYear`がなくても実行契約は変わらない。ただし、`examYear`がある公式過去問では独自問題専用の`05_originalized`を自動的に非適用とし、`examYear`がない問題だけを05の対象にする。
- `補足質問と回答`だけを選ぶ場合、`explanationText`は候補判断の参照用であり、更新できるのは`suggestedQuestionDetailsByChoice`だけである。他の工程も、選択したupdate targetの`fields`だけを書き換えられる。
- 相互に整合させる必要があるfieldは一つのupdate targetとして選ぶ。modelが選択外fieldをset又はunsetした候補は問題単位で拒否し、patchへ反映しない。
- preview tokenとrun receiptには`selectedUpdateTargetIds`、`selectedFieldsByStage`、`readFieldsByStage`を保存する。旧runの`questionRange`は再開互換のため読み取るが、新規runの画面からは指定しない。再開時に実行条件が一つでも違う場合は別runとして確認し直す。

### `artifactSync`

`artifactSync`はpatch確定後のMerge、Convert、upload-ready、upload dry-runだけを表します。

| 契機 | 自動実行 | 手動導線 |
| --- | --- | --- |
| 画面でpatchを保存 | 保存ごとに実行 | 自動更新失敗時の`パッチ変更を反映` |
| 一問queue | 全item走査後、確定した年度又はフォルダごとに1回実行 | 自動更新失敗時は管理機能の`出力` |
| 成果物が現在patchと一致 | 何もしない | 管理ツール内に非常用の強制再生成だけを残す |

完了状態は`succeeded`、`current`、`not_required`です。それ以外は更新待ちとして理由を表示します。

旧工程版と混在する年度では、現行03b済みの問題だけを再生成前後で検証します。法令関連問題がすべて現行03bになった年度は、mergedとFirestore成果物の全対象を検証します。

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
- serverは問題の現在projectionをtoken量で束ねる。1 turnは最大50問、同時turnは最大32本を安全上限とするが固定値ではない。provider又はschema失敗時は自動で束と並列数を縮小し、安定時だけ拡大する。UIはこの自動設定だけを表示する。
- modelは問題別の構造化候補を返すだけで、検査commandや成功receiptを自己申告しない。serverは候補ごとにsource identity、許可field、工程品質、`00_source`不変を検査し、合格recordだけを確定patchへ反映する。同じturn内の他問題の不合格や曖昧さは波及しない。
- 初期対象外の先行工程はitemを作らず、その問で最初に必要な工程から始める。writerが確定したpatchは、物理Mergeを挟まず共通projectionで次工程へ渡す。patchが実際に変わった時だけ初期対象外の後続を再判定し、準備後の手動変更も最新入力で再準備する。一問の失敗は理由付き`blocked`とし、その問の依存後続だけを保留する。対象外は`not_applicable`で閉じ、他問を止めない。
- 正本文書又は工程版がrun中に変わった場合は、その問題だけを最新projectionでqueueへ戻す。通常対象を先に終え、不合格問題はfeedback付きでqueue末尾へ回す。品質検査は初回を含む3回で打ち切る。
- 一問を安全に破棄又はrollbackできる失敗は他問へ波及させない。候補内容の不備はその問だけ、turn全体のprovider・schema失敗はその束だけをqueue末尾へ戻す。回復しなければ`interrupted`として再開を待つ。
- 複数問turnでも一問の確定ごとにcheckpointを保存する。`未完了の問題を再開`はそのcheckpointを親queueへ回収し、未確定の問だけを戻す。工程の方針fingerprintが欠けるitemは確定済みとみなさず再検査する。rollback又は残存差分を確認できないrunは再開せず、成果物同期もしない。
- 物理Merge、Convert、upload-ready、upload dry-runはqueue終了時に確定したlistGroupIdごと1回だけ実行する。失敗してもpatchは保持し、更新待ちのときだけ手動再生成を表示する。

評価と再評価は問題ごとの新しいread-only thread、再整備は問題ごとの新しいworkspace-write threadで実行し、異なる作業でthreadを再開・forkしません。

開始前にChatGPT固定枠、利用上限、追加creditなし、公式provider、Standard tierを確認します。API key、従量課金、外部MCP・plugin・app・hook・browser操作は使いません。調査は隔離したread-only threadと組み込みweb検索に限り、保存は`multi_agent=false`のthreadが担当します。

## 作業バージョン

工程版は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の`policy_version`だけを`MAJOR.MINOR`形式で管理します。洗い替え不要の改訂はMINOR、必要な改訂はMAJORを上げます。公開済みだが使用版を証明できない初期値は`v0.0`です。

run開始時とreceipt検証時に、完全な版番号と正本文書fingerprintを照合します。全更新項目を実行した場合は工程単位、部分実行ではupdate target単位で、成功receiptを検証した対象だけを`work_versions.json`へ記録します。未選択のupdate targetは現行版になりません。`stateHash`変更又は現行MAJOR未満は再整備、評価版のMAJOR変更は再評価の対象です。

## 進捗、heartbeat、技術ログ

- `progress.jsonl`は、問題ごとに`question_started`、`policyTargets`順の`stage_completed`、`question_completed`を直後に追記する。`policyTargets`には現在runの正式な問題IDだけを保存し、aliasや旧runのIDを補完しない。順序違反、重複、対象外工程は無効であり、完了数へ含めない。
- `processed`は全イベントがそろった状態、`validated`は成功receiptをserverが確認した状態である。停止時のprocessed出力は`未承認`とし、完了表示や作業版記録に使わない。親runは必要な全子工程がvalidatedになった問題だけを完了とする。
- Codex App Serverのturn待機中は15秒間隔で`heartbeatAt`を更新する。子runのheartbeatは親runとjobの`lastActivityAt`へ伝播するが、問題処理又はreceipt検証の完了を意味しない。
- runごとの`technical_log.jsonl`はappend-onlyで、`sequence`、`observedAt`、`level`、`message`を保存する。該当時は`commandStatus`、`exitCode`、`outputTail`、repository相対`changedPaths`も保存する。同一イベントを重複記録せず、秘密情報と思考過程を除く。
- 通常のrun・job APIは要約だけを返す。技術ログは`GET /api/qualification-runs/<runId>/technical-log?qualification=<qualification>`から、画面で展開中だけ取得する。

画面は一つのpoll管理でrun、job、進捗を更新し、実行dialog表示中は背景pollを止めます。問題は分野・問題番号とsource上の自然な順序で表示し、processedとvalidatedを分けます。進捗から問題を開く「作業対象を確認」には、問題文・選択肢・正答・解説とpatch適用後の`questionType`、問題整備専用の`isCalculationQuestion`を表示します。`flash_card`と`group_choice`の基本解説は問題共通の1本として選択肢一覧の上に表示し、選択肢カードへ繰り返しません。問題の詳細画面では、選択肢をタップすると、その選択肢の`suggestedQuestionDetails`に相当する質問と回答だけをカード内に表示します。`suggestedQuestionDetailsByChoice`が0件の選択肢も、保存済み補足がないことを明示します。補足0件は不備ではなく、基本解説と重複する候補を保存しない正規状態です。旧flat fieldしかない場合は「選択肢未割当・再生成が必要」と表示し、推測で割り当てません。

監査画面の「00_sourceと正答が異なる」は、`00_source.correctChoiceText`とpatch適用後の`correctChoiceText`を問題単位で比較して絞り込みます。`○`と`正しい`、`×`と`間違い`は同じ判定として扱い、実質的に変わった選択肢だけを詳細画面へ表示します。この差分は確認対象であり、それだけで整備失敗又は公開不可とは判定しません。

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
