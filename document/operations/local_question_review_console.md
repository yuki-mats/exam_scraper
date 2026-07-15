# 問題整備システム

この文書は、ローカルGUIから整備・評価・再整備を実行する境界の正本です。全体の流れは[問題整備ワークフロー](exam_pipeline_manual_and_automation.md)、GUIの工程順とpromptは[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照します。

## 構成

```text
browser -> Python server -> Codex App Server（stdio）
```

Python serverがChatGPT app同梱binaryの`codex app-server` processを一つ管理します。画面の`整備を開始`、`評価を開始`、`再整備を開始`は、すべて`thread/start`と`turn/start`へ変換します。PATH上の別binary、`codex exec`、OpenAI Platform API、外部model providerへはfallbackしません。

## サブスクリプション境界

接続時と各thread開始前に`account/read`と`account/rateLimits/read`を確認し、次をすべて満たす場合だけ開始します。

- `account.type = "chatgpt"`で、固定枠のChatGPT planである。
- 利用上限へ達していない。
- `credits.hasCredits = false`である。
- 追加支出を許す`individualLimit`がない。
- model providerが`openai`で、service tierがStandardである。

API key、usage-based plan、追加credit、判定不能な状態では開始しません。Fast modeとrate-limit reset creditは使いません。子processからAPI key環境変数も除外します。認証と課金区分の正本は[Codex authentication](https://learn.chatgpt.com/docs/auth)と[Codex pricing](https://learn.chatgpt.com/docs/pricing)です。

有効configを実行前に読み、`forced_login_method = "chatgpt"`、公式接続先、外部機能の無効化を確認します。local commandのnetworkと親process環境の継承、MCP、plugin、app、hook、browser/computer操作、host通知command、analytics、OpenTelemetryは無効です。整備threadのcwdはrepository外の一時directoryとし、対象groupのpatch層と当該run receiptだけを`writableRoots`にします。調査はCodex組み込みweb検索でe-Gov又は所管官庁の一次情報を開きます。Firestore、Storage、GitHub、外部有料APIをcommand又はMCPから呼び出しません。

## session分離

| 作業 | Codex thread | sandbox | 入力 |
| --- | --- | --- | --- |
| 整備 | UIで開始したrunごとに新規 | workspace-write | 現在の対象、正本文書、工程prompt |
| 評価 | 元問題1問ごとに新規 | read-only | 現在の1問と根拠候補 |
| 再整備 | 不合格問題ごとに新規 | workspace-write | 現在の問題と構造化評価結果 |
| 再評価 | 元問題1問ごとに再び新規 | read-only | 再整備後の現在の1問 |

異なる作業で`thread/resume`又は`thread/fork`を使いません。評価threadはrepo外の空の一時directoryから開始し、過去の整備prompt、review、評価結果のpathを渡しません。

初期実装は`approvalPolicy = "never"`です。workspace内の通常処理だけを許し、sandbox外のcommand、file変更、追加permission要求は自動承認せず拒否します。

## 作業バージョン

工程ごとの現行版は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の`policy_version`だけで管理します。全工程共通の版や、再整備専用の版は作りません。

版を上げる判断は一つです。

> 同じ入力でも判断又は出力が変わり得る変更なら、該当工程を1つ上げる。

- 影響する工程だけを`+1`する。複数工程へ影響する場合は、その各工程をそれぞれ`+1`する。
- 誤字、体裁、説明の明確化など、判断と出力が変わらない変更では上げない。
- 再整備は、修正した01、02、02a、02b、03、03b又は04の現行版を記録する。`再整備vN`は作らない。
- 評価基準、評価prompt又は評価JSON Schemaが変わる場合は評価版だけを`+1`する。
- 資格文書は、同configの`qualification_document_patterns`で紐づく工程だけに影響する。

各runは版番号に加えて正本文書のfingerprintを持ちます。開始時と成功receipt検証時のfingerprintが異なるrunは失敗にし、途中で判断ルールが入れ替わった結果を記録しません。過去問の洗い替え判定は版番号だけで行うため、判断と出力が変わらない誤字・体裁変更では既存問題を旧版にしません。判断又は出力が変わり得る変更では、担当者が該当工程の`policy_version`を必ず`+1`します。

成功receiptを検証した後だけ、対象の各問へ「どの工程を何版で実施したか」を記録します。失敗・中断・receipt不備では記録しません。法令監査版は法令問題だけに適用します。既存公開問題の初期値`v0`は「過去に作業済みだが使用版を証明できない」を表し、現行版への洗い替え対象です。

`stateHash`は問題内容の新しさ、作業バージョンは判断ルールの新しさを表します。公開には、適用対象の全整備工程が現行版であること、現在内容に対する現行評価版の合格、機械品質ゲートのすべてが必要です。GUIでは資格、年度・フォルダ、選択工程の作業バージョンを組み合わせ、`旧版・未記録のみ`を実行できます。

## 客観評価

- 問題文と全選択肢を一問ずつ確認する。
- 現在の正答対応と公式正答は評価promptへ渡さず、全肢の独立判定後にPython serverが現在値との一致だけを計算する。
- 現在の解説は採点対象に限定し、選択肢の正誤根拠として扱わない。
- 一次資料、公式資料、法令本文又は独立計算で各選択肢を確認する。
- 根拠不足は`insufficient_evidence`として不合格にする。
- 指定JSON Schemaへ限定し、serverが全選択肢、正答対応、解説点数、重大指摘を再検証する。

問題の`stateHash`又は評価版が変わると以前の評価は自動で古くなります。再整備後は、新しい評価threadに合格するまで公開できません。

## 保存と安全境界

各sessionは`output/question_review_console/workflow_runs/<qualification>/<runId>/`へ`manifest.json`、`prompt.md`、`result.json`を保存します。整備・再整備では、再起動回収用の`baseline.json`も保存し、`result.json`だけを`agent_output/`配下へ分離します。manifestには`workType`、`sessionId`、`threadId`、`turnId`、対象、`stateHash`、`policyVersions`、`policyFingerprints`、sandbox、状態と時刻を記録します。評価の最新表示だけは資格・年度配下の`evaluations/`へ投影します。

問題ごとの工程履歴は`output/question_review_console/<qualification>/<listGroupId>/work_versions.json`へ保存します。各工程は最新記録と過去の`history`を持つため、洗い替え後も`v0`などの旧記録を追跡できます。これは運用メタデータであり、`00_source`、patch、merged、upload-ready、Firestore question documentへ複製しません。公開済み問題の一括初期化receiptは`output/question_review_console/work_version_backfills/<timestamp>/manifest.json`へ保存します。

- 整備と再整備の前後で`00_source`不変検証を行う。
- 整備と再整備は工程又は選択fieldに対応するpatchだけを変更する。1問指定では対象patch fileとJSON/JSONL内の対象recordも限定し、App Server通知、実行前後のrepository差分、receiptの`changedFiles`を双方向で照合する。
- `questionBodyText`と`choiceTextList`はCodex自動整備の対象外とする。両fieldはblind reviewと根拠を必須にする`24_questionIssueCorrections`専用workflowで扱う。
- 実体patch、評価projection、readback、merge、sync、公開artifactを変更する処理は、システム全体で1件ずつ実行する。一意IDのreview・run metadataは開始前にatomic writeし、排他競合時はfailed又は`needs_review`へ戻す。
- 評価threadはfileとFirestoreを変更しない。Python serverだけがrun receiptと最新評価projectionを保存する。
- Firestore反映はCodex threadへ任せず、既存preflight、UIの明示確認、直後のreadbackを使う。
- app-server停止、認証不一致、利用上限、receipt不備は安全な失敗として保存する。
- 失敗又は中断turnの変更と削除はfailed receiptへ残し、merge・convert・問題単位を含む公開をブロックする。再実行で変更したpath、又は内容を検証して`resolvedFailedDeltaPaths`へ明示したpathだけを解除する。

## 起動

```bash
python3 tools/question_bank/question_bank.py review-ui
```

serverは`127.0.0.1`だけへbindします。本人端末から使う場合だけTailscale Serveのprivate HTTPSを使います。Codex App Server protocolの正本は[Codex App Server](https://learn.chatgpt.com/docs/app-server)と、使用中binaryから生成したschemaです。
