# 問題整備システム

この文書は、ローカルGUIから整備・評価・再整備・公開を実行するときの順序と安全境界の正本です。工程順とpromptは[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照してください。

## 手戻りを防ぐ運用順序

1. 実装・文書・設定の変更とテストを終えてserverを再起動する。run中は確定中のpatchと作業版台帳を外部から変更しない。
2. トップの年度・フォルダ（`listGroupId`）一覧で`整備・洗い替え`を開き、対象範囲、整備する項目（初期状態は全選択）、処理する問題（初期値は`整備が必要な問題だけ`）を指定する。工程は選択項目から自動で決まり、serverが一問queueへ分解して`00_source`と確定patchの論理projectionを渡す。
3. serverは[幹の基本仕様](exam_pipeline_manual_and_automation.md#一問整備の基本仕様)に従い、設定された同時処理数で各問を独立に検査・確定する。
4. patch確定後は[`artifactSync`](#artifactsync)で公開用成果物を自動更新する。失敗時だけ手動再生成を使う。
5. 成果物が最新になった後、年度カードの`評価・公開`（管理表示 `mode=manage`）から別sessionで評価する。合格した問題を1〜100問明示選択し、確認後に指定順で一問ずつFirestoreへ反映して各問のreadback一致を確認する。
6. 進捗と整合性監査は画面表示を証跡にせず、`manifest.json`、`progress.jsonl`、`technical_log.jsonl`、問題別projection、`result.json`、完了receipt、readbackを直接確認する。並列稼働のread-only観測には[バッチ成果物モニター](batch_artifact_monitor.md)を使う。

## 確定、rollback、再生成

| 状態 | 完了条件 | 後続失敗時 |
| --- | --- | --- |
| patch確定 | 成功receipt、変更範囲、工程検証、`00_source`不変、作業版台帳を検証し、`receiptValidated=true`になった | 確定前ならrollbackする。 |
| 公開用成果物 | `artifactSync`と公開前の機械gateが成功し、現在のpatchと一致した | patchは取り消さず、再生成だけをやり直す。 |
| Firestore反映 | 現行工程版と別session評価に合格し、明示確認後のreadbackが一致した | ローカル成功だけで反映済みにしない。 |

### トランザクションと直接修正・洗い替え境界

- **整備runのfile transaction**: modelはnative JSONのcandidate v3だけを返し、identity解決・field制限・工程検査・保存はserverが所有する。合格した一問だけを最新patchと`work_versions/<reviewKey hash>.json`の双方で実path lockして開始前bytesをbaselineへ保存し、同じcommit点で確定する。失敗時はlock解放前に開始前へ戻し、差分が残らなければ兄弟問題を続行する（rollback不能又は共有状態破損だけが親queue停止理由）。
- **画面からの直接修正**: 対象fileの開始前bytesとtransaction manifestを`output/question_review_console/direct_edit_transactions/`へ先に保存してから更新し、途中失敗や開始前bytesとの差分（failed delta）残存時は成功扱いにしない。patch保存がcommit点であり、後続のcache更新失敗時はpatchを戻さず`warning`と`postCommitErrors`を返す（Firestoreへは書き込まない）。
- **項目を限定した洗い替え**: 更新項目は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の`update_targets`だけを正本とする。自然言語指示もcatalog上の構造化計画へ変換し、選択外fieldを変更した候補は問題単位で拒否する。現在内容hashに一致する未解決の一問reviewがあれば対象項目に関する指摘だけを入力へ渡す。年度・フォルダ識別には`listGroupId`を使い、`examYear`がある公式過去問では`05_originalized`を自動非適用とする。未完了runの再開（`未完了を再開`）、前回対象を除いた分割実行、基準更新後の`同じ対象を現行基準で再整備`もpreview tokenと開始receiptに対象ID集合・更新fieldを固定して実行する。

### `artifactSync`

`artifactSync`はpatch確定後のMerge、Convert、upload-ready、upload dry-runだけを表し、未確定差分台帳は意味上のpatch層・`99_model_review_flags`・`review`だけを追跡します。

| 契機 | 自動実行 | 手動導線 |
| --- | --- | --- |
| 画面でpatchを保存 | 保存ごとに実行 | 自動更新失敗時の`パッチ変更を反映` |
| 一問queue | 全item走査後、確定した年度又はフォルダごとに1回実行 | 自動更新失敗時は管理機能の`出力` |
| 成果物が現在patchと一致 | 何もしない | 管理ツール内に非常用の強制再生成だけを残す |

完了状態は`succeeded`、`current`、`not_required`であり、それ以外は更新待ちとして理由を表示します。

## 問題IDと現行法監査

- `uiQuestionId`と`reviewKey`は画面操作用であり、03bの監査sidecar（`law-revision-audit/v2`）はsource由来の`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`（`<path>#<index>`）の3要素完全一致で結合する。`reviewKey`が衝突しても`sourceRecordRef`で分離表示し、3要素を一意確定できない問題だけ03bをfail-closedでblockする。03b開始前には旧sidecarのUI IDを3要素へ原子的正規化する。
- Convert及びupload-readyは、`00_source`の一意な既存Firestore IDとsource結合patchの公開IDを併合して対応付け、`00_source`由来IDを捨てない。
- 技術知識や計算だけで決まる問題は`isLawRelated=false`、`auditStatus="not_law_related"`、`reviewState="secondary_verified"`で03b完了を記録でき、法令根拠不在だけで`hold`にしない。03b確定前には工程03と同じ日本語品質検証及びsidecar・`lawRevisionFacts`・正答対応・verified根拠の検証を必須とする（詳細は[現行法監査](current_law_question_maintenance_workflow.md)と[03b prompt](../../prompt/03b_prompt_audit_current_law_and_patch.md)参照）。

## 一問queueとsession

- **モデルと並列制御**: 接続・上限の正本は[`config/question_maintenance_llm.toml`](../../config/question_maintenance_llm.toml)である。既定profileは`codex_only`（候補生成・再試行はGPT-5.6 Luna、独立review・評価はGPT-5.6 SolのCodex App Server）とし、監査不合格時は検査feedbackをLunaの一問queueへ返す。実効同時数はUI選択値・`question_parallelism`・`llm_call_concurrency`の最小値によるローリングwindowで制御し、サブスクリプション枠や承認済み既存credits残高の範囲内で実行する（自動チャージやAPI従量fallbackは使わない）。
- **第01工程の二段階確認**: 第01工程は、全問題に対して同じsource snapshotを使う独立したread-onlyレビューを2回実行し、serverが結果を照合してから通常の問題形式候補を生成する（不一致時は一度だけread-only裁定を実行し、判定不能・境界不明・hash不一致は問題単位の`hold`とする）。レビューの詳細schemaはproductionコードを正本とし、この文書には複製しない。合意済みspanからの本文切り出しはserverが決定的に行う。
- **進行と状態分離**: 不変の対象・工程契約は`plan.json`、一問の可変状態は`questions/<questionIdのsha256>.json`（`question-maintenance-question/v3`、大きな不変payloadは`attempt_payloads/`へ分離）、表示用集計は60秒周期で更新する派生`question_summary.json`へ分離する。先行工程の確定patchは物理Mergeを挟まず共通projectionで次工程へ渡す。品質検査不合格はfeedback付きで最大3回（初回＋再試行2回）まで試行し、失敗問は`blocked`として依存後続だけを保留して他問を進めさせる。source本文更新で分解hashが変わった場合は旧分解patchを引き継がず01から再判定する。

## 作業バージョン

工程版は[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)の`policy_version`だけを`MAJOR.MINOR`形式で管理します（洗い替え不要はMINOR、必要はMAJOR更新、未証明の公開済み初期値は`v0.0`）。成功receiptを検証した対象だけを`output/question_review_console/<qualification>/<listGroupId>/work_versions/<reviewKeyのsha256先頭24桁>.json`へ一問ずつ記録します。`stateHash`変更又は現行MAJOR未満は再整備、評価MAJOR変更は再評価の対象です。

## 進捗、heartbeat、技術ログ

- `progress.jsonl`は問題ごとに`question_started`、`policyTargets`順の`stage_completed`、`question_completed`を追記する。全イベント揃いが`processed`、serverのreceipt検証完了が`validated`であり、必要な全工程が`validated`になった問題だけを完了とする。
- Codex App Server待機中もcoordinatorが60秒間隔で`heartbeatAt`を更新する（完了は意味しない）。単一turnの30分超過や完了通知欠落時は該当問だけを安全に中断・検査する。
- `technical_log.jsonl`はappend-onlyで`sequence`、`observedAt`、`level`、`message`及び該当時の`commandStatus`、`exitCode`、`outputTail`、`changedPaths`を秘密情報・思考過程を除いて保存し、画面展開時だけ`GET /api/qualification-runs/<runId>/technical-log?qualification=<qualification>`で取得する。
- **画面表示契約**: トップの`問題一覧を見る`は読取専用とし、一覧・詳細は別プロセス生成の派生read model cacheから高速表示して`最終更新`（sourceと適用patchの最新更新日時）を示す。`flash_card`と`group_choice`の基本解説は問題共通の1本として選択肢一覧の上に表示する。問題詳細では、選択肢をタップすると、その選択肢の`suggestedQuestionDetails`に相当する質問と回答だけをカード内に表示する。補足0件は不備ではなく、基本解説と重複する候補を保存しない正規状態である。`questionBodyText`と`choiceTextList`の選択reviewは記録専用（自動patch対象外）とする。

## 検査feedbackと改善記録

機械チェック不合格時はその問・工程に限ったfeedback付きで最大2回再整備し、`validationAttempts`と技術ログへ記録します。未確定patchが成果物同期を止める場合は`パッチ変更を反映`からbaseline照合の上で清掃します（実質的な内容指摘は再整備へ残す）。queue終端後は`improvement_report.json`へ指摘を集計し、3問以上の反復指摘やserver拒否を別jobでの正本・checker・`policy_version`改善候補とします。

## 評価と公開の安全境界

- **独立評価**: 評価は生成とは別sessionのread-only監査turn（単問又は同条件最大5問batch）で、`questionType`・計算判定・問題文・全選択肢（宣言画像の`imageInputReceipt`証跡を含む）を一問ずつ独立判定する。現在の正答は先に渡さず、検証済みjudge欄や`isOfficial: true` snapshotは取得元正答証拠として分離渡しする。`true_false`は各肢を独立公開するため正答数だけでは不合格にしない。全肢未検証は`inconclusive`として評価待ちへ戻し、現行有効評価は`evaluations/<reviewKeyHash>.json`の`currentValid`で管理する（限定対象の事前内容評価CLIは`publicationReady=false`の確認専用であり公開承認にはならない）。
- **要再整備と公式冊子照合**: `要再整備`の問題は複数選択して問題整備runへ戻し、現在の`stateHash`と一致する指摘を該当工程（05・01+03・02a・03・03b）へ渡す。公式問題の本文・選択肢の誤記補正は通常整備では行わず、公式問題冊子がある場合に限り`公式冊子と照合`（Blind A/BとChallenge）を通過した`fix`だけを`24_questionIssueCorrections`へ保存する（[公式問題の問題報告 workflow](question_issue_report_workflow.md)参照）。
- **Firestore公開**: 公開はCodex threadへ任せず、最大100問の明示選択とpreflight確認後に`repository排他`job内で一問ずつ実行し、直後のlive readback一致を確認する（複数資格評価jobも`repository排他`を維持し、単一資格の整備・評価は資格単位排他で並行可能）。

## 起動

`.venv/bin/python tools/question_bank/question_bank.py review-ui`で起動します（`127.0.0.1` bind、process lease及び資格単位leaseで二重起動と競合回収を防止し、`workflow_runs/*/*/recovery.json`の実行中runだけを安全に回収）。保存済みnativeレビューの限定取込は`QualificationRunCoordinator.import_scoped_native_evidence`で既存validatorを通してprivate import runへ記録します。
