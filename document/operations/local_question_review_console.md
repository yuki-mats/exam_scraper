# 問題整備システム

この文書は、ローカルGUIから整備・評価・再整備を実行するときの順序と安全境界の正本です。工程順とpromptは[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照してください。

## 手戻りを防ぐ運用順序

1. 実装・文書・設定の変更とテストを終え、serverを再起動する。run中は外部からfile編集、commit、pushを行わない。
2. トップの`未整備を整備`から資格・年度（回）・工程を指定する。serverが対象を一問queueへ分解し、確定済みpatchだけを次工程へmergeする。
3. 失敗時は停止理由に挙がった問題から直す。問題詳細の`パッチを修正`、`修正を依頼`又は`保留問を再実行`を使い、確定済み問題はやり直さない。
4. patch確定後は[`artifactSync`](#artifactsync)で公開用成果物を更新する。更新待ちになった場合だけ手動再実行する。
5. 公開用成果物が最新になった後、別sessionで評価する。合格した問題だけを明示操作でFirestoreへ反映し、readback一致を確認する。

## 確定、rollback、再生成

| 状態 | 完了条件 | 後続失敗時 |
| --- | --- | --- |
| patch確定 | 成功receipt、変更範囲、工程検証、`00_source`不変、作業版台帳を検証し、`receiptValidated=true`になった | 確定前ならrollbackする。 |
| 公開用成果物 | `artifactSync`と公開前の機械gateが成功し、現在のpatchと一致した | patchは取り消さず、再生成だけをやり直す。 |
| Firestore反映 | 現行工程版と別session評価に合格し、明示確認後のreadbackが一致した | ローカル成功だけで反映済みにしない。 |

### 整備runのfile transaction

- GUI内のpatch、作業版、merge、sync、評価projection、readback、公開artifactを変更する処理は、共通のrepository排他で1件ずつ実行する。run中に同じrepositoryを外部から手編集すると、安全なrollbackを保証できないため禁止する。
- serverは許可された書込fileの開始前bytesと「存在しなかった」事実をrunのbaselineへ保存する。`receiptValidated=true`前の失敗・中断・server再起動では、そのrunが変更したfileを開始前状態へ戻す。
- patchと`work_versions.json`の更新は同じ確定処理で扱う。作業版台帳の記録又はmanifest更新に失敗した場合は、どちらも未確定とし、file transactionでrollbackする。
- rollbackできないpathだけを未確定差分として公開処理からblockする。failed deltaの対象、責任工程、解除可否はserverがbaselineと現在bytesから決定し、Codexのreceiptや利用者入力では解除しない。
- `receiptValidated=true`がcommit点である。以後の`artifactSync`が失敗又は中断しても、確定patchと作業版をrollbackしない。

### 画面からの直接修正

直接修正は、対象fileのbaselineとtransaction manifestを`output/question_review_console/direct_edit_transactions/`へ先に保存してから、全fileを更新します。途中失敗では全fileを戻し、再起動時にも未完了transactionを回収します。

patch保存がcommit点です。その後のcache無効化、確認記録又は再読込に失敗してもpatchを戻さず、画面へ`warning`と`postCommitErrors`を返します。本番Firestoreへは書き込みません。

### `artifactSync`

`artifactSync`はpatch確定後のMerge、Convert、upload-ready、upload dry-runだけを表します。

| 契機 | 自動実行 | 手動導線 |
| --- | --- | --- |
| 画面でpatchを保存 | 保存ごとに実行 | 問題詳細の`パッチ変更を反映` |
| 一問queue | 全item走査後、確定した年度ごとに1回実行 | 管理機能の`出力` |
| 失敗・中断 | patchを保持して更新待ちにする | 理由を解消して上記導線から再実行 |

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

Python serverはChatGPT app同梱の`codex app-server`を一つ管理します。PATH上の別binary、`codex exec`、OpenAI Platform API、外部model providerへfallbackしません。整備、評価、再整備、再評価は`gpt-5.5`、推論強度`high`をturnごとに指定し、返された実modelとともにmanifestへ保存します。

- GUIの開始範囲は資格・年度（回）・工程のままとし、serverが`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`と工程の組へ分解する。一問だけ残る場合も同じqueueを使う。03cの分類準備だけは資格全体の前提工程とする。
- 各問の判断案は隔離したread-only threadで最大2問まで準備し、準備できた問から単一のworkspace-write threadが一問・一工程ずつpatch、検証receipt、作業版を確定する。
- 一問の失敗は理由付き`blocked`とし、その問の依存後続だけを保留する。再計画で対象外になった工程は`not_applicable`で完了させ、未確定のまま残さない。年度別mergeの失敗も、その年度の後続だけを保留する。
- `保留問を再実行`は同じ範囲と工程を引き継ぐ。確定済みで入力・方針fingerprintが一致するitemだけを飛ばし、保留・中断・入力変更itemを再queueする。工程間mergeが未完了なら、その年度だけを先に再mergeする。ただしrollback又は残存差分を確認できないrunは再開させない。

評価と再評価は問題ごとの新しいread-only thread、再整備は問題ごとの新しいworkspace-write threadで実行し、異なる作業でthreadを再開・forkしません。

開始前にChatGPT固定枠、利用上限、追加creditなし、公式provider、Standard tierを確認します。API key、従量課金、外部MCP・plugin・app・hook・browser操作は使いません。調査は隔離したread-only threadと組み込みweb検索に限り、保存は`multi_agent=false`のthreadが担当します。

## 作業バージョン

工程版は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の`policy_version`だけを`MAJOR.MINOR`形式で管理します。洗い替え不要の改訂はMINOR、必要な改訂はMAJORを上げます。公開済みだが使用版を証明できない初期値は`v0.0`です。

run開始時とreceipt検証時に、完全な版番号と正本文書fingerprintを照合します。成功receiptを検証した対象だけを`work_versions.json`へ記録し、履歴を残します。`stateHash`変更又は現行MAJOR未満は再整備、評価版のMAJOR変更は再評価の対象です。

## 進捗、heartbeat、技術ログ

- `progress.jsonl`は、問題ごとに`question_started`、`policyTargets`順の`stage_completed`、`question_completed`を直後に追記する。順序違反、重複、対象外工程は無効であり、完了数へ含めない。
- `processed`は全イベントがそろった状態、`validated`は成功receiptをserverが確認した状態である。停止時のprocessed出力は`未承認`とし、完了表示や作業版記録に使わない。親runは必要な全子工程がvalidatedになった問題だけを完了とする。
- Codex App Serverのturn待機中は15秒間隔で`heartbeatAt`を更新する。子runのheartbeatは親runとjobの`lastActivityAt`へ伝播するが、問題処理又はreceipt検証の完了を意味しない。
- runごとの`technical_log.jsonl`はappend-onlyで、`sequence`、`observedAt`、`level`、`message`を保存する。該当時は`commandStatus`、`exitCode`、`outputTail`、repository相対`changedPaths`も保存する。同一イベントを重複記録せず、秘密情報と思考過程を除く。
- 通常のrun・job APIは要約だけを返す。技術ログは`GET /api/qualification-runs/<runId>/technical-log?qualification=<qualification>`から、画面で展開中だけ取得する。

画面は一つのpoll管理でrun、job、進捗を更新し、実行dialog表示中は背景pollを止めます。問題は分野・問題番号とsource上の自然な順序で表示し、processedとvalidatedを分けます。

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
