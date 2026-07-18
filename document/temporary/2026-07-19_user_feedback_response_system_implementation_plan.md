# ユーザーフィードバック対応システム実装計画

作成日: 2026-07-19

対象正本: [ユーザーフィードバック対応システム](../operations/user_feedback_response_system.md)

状態: 実装前

この文書は実装順、変更候補、テスト、完了条件を固定する一時計画です。継続仕様は対象正本だけへ反映します。この文書作成では実装コード、設定、service、clean cloneを変更しません。

## 1. 現行実装との差分

現行の[公式問題の問題報告workflow](../operations/question_issue_report_workflow.md)には、intake schema、カテゴリ分離、blind A/B、challenge、一次根拠、correction overlay、Git、Firestore readbackの基礎があります。一方、目標仕様と次が異なります。

| 関心事 | 現行 | 目標 |
| --- | --- | --- |
| 起動 | 運用者が手動棚卸し | Macログイン時に常駐し、自動検知 |
| AI経路 | stdin/stdoutの任意reviewer command | 既存と同じCodex App Serverだけ |
| 人間承認 | category snapshotを一括承認 | 検証済みcaseをスマホで一件ずつ判断 |
| 未承認案 | batch manifest内 | merge対象外のcontent-addressed proposal |
| patch確定 | batch実行中に生成 | 5秒取消し後、承認hashを照合して昇格 |
| Git | publish処理の一部 | 承認済みcorrection unitごとにcommit・push |
| Firestore | 同じproduction runで公開 | 既存問題公開フローの次回mergeへ合流 |
| 表示対象 | CLIのカテゴリ集計 | 全case又は修正候補のみを手動切替 |
| アプリ原因 | operational result | 将来用の独立レーンへ保存 |
| 改善 | batch結果 | 人間判断との精度指標と改善案 |

実装は現行の安全契約を捨てず、orchestration、UI、patch確定、公開境界を置き換えます。現行CLIはmigration完了まで残し、新systemが同じcaseを二重claimしないgateを設けます。

## 2. 実装方針

- 新systemは`tools/user_feedback_console/`へ置き、問題整備システムとは独立したserver、worker、static UIを持たせます。
- `tools/question_review_console/codex_app_server.py`、projection、patch checker、Firestore readbackなど、責務が一致する既存実装を再利用します。コピーして分岐させません。
- lane、priority、手動表示mode、retry回数、stateは新しい`config/user_feedback_response_system.toml`へ集約します。公式問題カテゴリと許可fieldは`config/question_issue_reports.json`を継続利用します。
- live caseの正本はFirestore、未承認proposalと技術ログは専用clean cloneのignored output、承認済みpatchはGitとします。同じ事実を複数の可変台帳へ重複保存しません。
- patch確定と既存公開フローの状態を分けます。patchがcommit済みでも、Firestore readback前は`公開済み`にしません。
- 全資格・全報告を最初のproduction enableで同時に有効化します。live pilotは行いません。fixture、回帰テスト、live read-only inventory、service readbackをfull enableのgateにします。

## 3. 実装work package

### WP1: target contractと状態migration

変更候補:

- `config/user_feedback_response_system.toml`を新設する。
- `config/question_issue_reports.json`のprotocol versionと`workflowStatuses`を目標状態へ更新する。
- Firestore storeへ`reviewing`、`ready_for_approval`、`approval_undo_window`、`approved_patch_pending`、`committed_publish_waiting`、`system_attention`を追加する。
- 既存`in_batch`、`publish_pending`を安全に読み、migration完了前のjobを失わない変換を実装する。
- proposal、approval、retry、archive、metricのschemaをJSON Schema又は厳格validatorで固定する。

主な対象:

```text
config/user_feedback_response_system.toml
config/question_issue_reports.json
tools/question_bank/question_issue_report_store.py
tools/user_feedback_console/contracts.py
tools/user_feedback_console/schemas/
tests/fixtures/user_feedback_system/
```

終了条件:

- 旧statusを含むfixtureを無損失で読み、新statusへ一度だけ移行できる。
- 同じcaseを現行batch CLIと新workerが同時claimできない。
- schema不明、hash欠落、複数カテゴリ混在をfail-closedで拒否する。

### WP2: 常駐intakeとclean clone

変更候補:

- `tools/user_feedback_console/worker.py`にstartup reconcileとFirestore watchを実装する。
- Macスリープ、network切断、watch切断、server再起動後に未完了caseを再開する。
- caseは一件ずつ処理し、同一caseのA/Bだけを並行実行するqueueを実装する。
- 専用clone path、branch、remote、clean状態、`origin/main`との差分を開始前に検証する。
- `launchd` templateとinstall/uninstall/status commandを追加する。installは`/Users/yuki/development/exam_scraper_feedback`が未作成又は正しいcloneであることを検証してから使う。

主な対象:

```text
tools/user_feedback_console/worker.py
tools/user_feedback_console/queue.py
tools/user_feedback_console/repository.py
tools/user_feedback_console/service.py
scripts/launchd/com.repaso.user-feedback-response-system.plist.template
tools/question_bank/question_bank.py
```

終了条件:

- Macログイン時に一つだけ起動し、異常終了後に再起動する。
- sleep中の新着を復帰後に重複なく処理する。
- dirty、detached HEAD、main以外、remote分岐時はwriterを止め、intakeと閲覧は継続する。
- 自動stash、branch作成、force push、他pathのrollbackを行わない。

### WP3: Codex App Serverによるblind review

変更候補:

- 既存`CodexAppServerClient`を使用し、AI-A、AI-B、challengeを別session境界で実行する。
- raw report、case ID、報告数、他reviewerの結果がblind promptへ入らない構造検査を継続する。
- route先の現行prompt本文とfingerprint、要求model設定、実model、session IDをmanifestへ保存する。
- 一次情報だけをevidenceに採用し、報告本文だけにあるURLを開かない。
- Codex App Server利用不能時はcaseを待機させ、API、外部provider、ローカルLLMへfallbackしない。

主な対象:

```text
tools/user_feedback_console/reviewer.py
tools/user_feedback_console/prompt_builder.py
tools/question_review_console/codex_app_server.py
prompt/question_issue_reports/
```

終了条件:

- A/Bが互いの出力と報告claimを見ないことをfixtureで証明する。
- A/Bの構造化変更と一次根拠が一致しないcaseを`fix`にできない。
- 実行に使った正本fingerprintと実modelをproposalから追跡できる。
- prompt injection、報告内URL、reporter情報が通常ログ又はpatchへ漏れない。

### WP4: 最新照合、proposal、事前gate

変更候補:

- 報告時snapshot、最新repo projection、Firestore readbackをidentityとhashで照合する。
- 内容変更時は旧reviewを無効化し、最新内容からA/Bをやり直す。
- correction unitを組み立て、関連fieldと同じ`originalQuestionId`のsiblingを原子的に扱う。
- logical merge preview、`check-question-issue-correction`相当、quality gateを正式patch作成前に実行する。
- merge対象外のproposalを`output/user_feedback_response_system/proposals/<caseId>/<proposalHash>/`へ原子的に保存する。

主な対象:

```text
tools/user_feedback_console/reconcile.py
tools/user_feedback_console/proposal.py
tools/user_feedback_console/preflight.py
tools/question_review_console/projection.py
scripts/check/check_question_issue_correction_patch.py
scripts/merge/question_issue_corrections.py
```

終了条件:

- 最新repoとFirestoreを一意に対応できないcaseを承認可能にしない。
- `00_source`、ID、許可外field、同値変更、壊れたsibling、古い`expectedBeforeHash`を拒否する。
- fix cardは全事前gateとmerge preview成功後だけ`ready_for_approval`になる。
- proposalの一byte変更でhashが変わり、旧承認を再利用できない。

### WP5: スマホ向け管理画面

変更候補:

- `tools/user_feedback_console/server.py`と`static/`を新設する。
- 件数一覧、health、filter、`全ケース`/`修正候補のみ`、影響度順の集中モードを実装する。
- 要約カードと展開詳細、field diff、一次根拠link、correction unit、事前gate、`Firestore未反映`を表示する。
- sanitized報告本文と同一報告数だけを表示し、reporter情報をAPI responseへ含めない。
- approve、no-change、hold、理由付きre-review、5秒undoを実装する。
- Tailscale Serveのprivate identityを検証し、Funnelとpublic requestを拒否する。追加認証は実装しない。

主な対象:

```text
tools/user_feedback_console/server.py
tools/user_feedback_console/static/index.html
tools/user_feedback_console/static/app.js
tools/user_feedback_console/static/styles.css
tools/question_bank/question_bank.py
```

終了条件:

- iPhone幅で横スクロールせず、主要判断と操作を一度で読める。
- 一覧から集中モードへ入り、処理後は次のcaseへ進み、一覧へ戻れる。
- 5秒以内のundoではpatch、Git、case終了の副作用がない。
- Tailscale接続だけで操作でき、public webから到達できない。
- notification、passkey、Face ID、PINを要求しない。

### WP6: patch昇格、Git、retry、訂正

変更候補:

- 5秒後にproposal、最新入力、policy fingerprintを再照合してから正式patchへ昇格する。
- baselineとtransaction manifestを先に保存し、複数fileの途中失敗を原子的にrollbackする。
- correction unitの正式patchだけをstageし、`main`へ一件ずつcommit、`origin/main`へpushする。
- transient failureだけを最大3回retryし、決定的検証失敗を即`system_attention`へ送る。
- 既存patchの削除ではなく、最新projectionを打ち消す新correction patchを作る訂正flowを実装する。

主な対象:

```text
tools/user_feedback_console/approval.py
tools/user_feedback_console/patch_writer.py
tools/user_feedback_console/git_writer.py
tools/user_feedback_console/retry.py
tools/question_review_console/write_transaction.py
```

終了条件:

- 承認したproposalと正式patchのhashが一致する。
- unrelated fileをstage、commit、rollbackしない。
- 同一内容のretryで再承認を求めず、内容変更時だけ承認を無効化する。
- 3回失敗後に手動対応待ちとなり、他caseは進行できる。
- 承認時にphysical merge、upload、Firestore question writeを実行しない。

### WP7: 既存公開フローとの合流、archive、metric

変更候補:

- `committed_publish_waiting`を問題整備システムのmerge対象として表示する。
- 既存publish/readback結果をcaseへ対応させ、target field一致後だけ`published`へ進める。
- no-change、hold、app-update、published、訂正関係を削除せず検索可能にする。
- AI判断と人間判断の一致、差し戻し、保留、訂正、待ち時間を資格・カテゴリ別に集計する。
- 改善候補artifactを作っても、prompt、checker、policyを自動変更しない。

主な対象:

```text
tools/user_feedback_console/publication_tracker.py
tools/user_feedback_console/archive.py
tools/user_feedback_console/metrics.py
tools/question_review_console/server.py
document/operations/local_question_review_console.md
document/operations/question_issue_report_workflow.md
```

終了条件:

- patch commit直後は`published`にならない。
- 次回の既存merge、Firestore書込み、live readback後に自動で`published`へ進む。
- 処理済み履歴をcase、question、qualification、category、commitで検索できる。
- report本文、reporter情報、思考過程をarchive、metric、Gitへ含めない。

### WP8: installと全件有効化

変更候補:

- 専用clean cloneを作成し、`main`と`origin/main`が一致することを確認する。
- `launchd`をinstallし、server再起動、Macログイン相当、異常終了復旧を確認する。
- Tailscale Serveのprivate HTTPSを設定し、スマホ実機で件数一覧とfixture承認を確認する。
- live Firestoreをread-onlyで棚卸しし、全資格・全報告がscopeに入り、未知schemaがないことを確認する。
- 全gate成功後、一度の切替で全資格・全報告のautomatic reviewを有効化する。

終了条件:

- 資格単位のproduction pilotを挟まず、full scopeが一度に有効になる。
- enable前に取得したcase数とenable後のqueued/reviewing/terminal合計が一致する。
- system停止中にもreportを失わず、再起動後にresumeできる。
- installation receiptとlive health readbackを保存する。

## 4. テスト計画

### 4.1 Unit test

| 対象 | 必須case |
| --- | --- |
| intake | 全カテゴリ、重複、複数カテゴリ分離、既処理同hash、内容変更 |
| state | 旧status migration、二重claim、再起動resume、不正遷移拒否 |
| blind review | raw field混入拒否、A/B独立、一次根拠限定、URL非追跡 |
| reconcile | repo/Firestore一致、既修正、hash drift、identity曖昧、missing |
| proposal | content address、policy drift、atomic write、private field排除 |
| gate | 許可外field、同値変更、`00_source`、ID、sibling、merge preview、quality |
| approval | 5秒undo、二重tap、期限切れ、stale proposal、理由付き差し戻し |
| Git | scoped stage、main以外拒否、dirty拒否、fast-forward、push retry |
| retry | transient 3回、deterministic即停止、hash変更時再承認 |
| archive | no-change、hold、published、訂正link、PII非保存 |
| metrics | 一致率、差し戻し率、保留率、訂正率、資格・カテゴリ集計 |

想定test file:

```text
tests/test_user_feedback_contracts.py
tests/test_user_feedback_worker.py
tests/test_user_feedback_reviewer.py
tests/test_user_feedback_reconcile.py
tests/test_user_feedback_proposal.py
tests/test_user_feedback_preflight.py
tests/test_user_feedback_server.py
tests/test_user_feedback_approval.py
tests/test_user_feedback_git_writer.py
tests/test_user_feedback_publication_tracker.py
tests/test_user_feedback_metrics.py
```

### 4.2 Integration fixture

`tests/fixtures/user_feedback_system/`に最低限次を用意します。

1. 正しい問題への誤指摘で`no_change`
2. 正答誤りで複数fieldを直す`fix`
3. A/B不一致の`hold`
4. 一次根拠不足の`hold`
5. 現行法と出題時正答が異なる法令case
6. grouped choiceの複数record correction unit
7. 報告後に正本が更新され、既に解消したcase
8. アプリ表示原因の`app_update`
9. prompt injection、URL、個人情報を含むreport
10. 5秒undo、retry、server再起動、Git競合、訂正patch

fixtureはFirestore、Codex App Server、Git remote、clockを制御可能にし、production question writeを行いません。

### 4.3 UIとsecurity

- iPhoneの狭幅、標準幅、横向きで一覧、カード、diff、展開詳細、buttonを確認する。
- 主要情報を開いた直後に読み、詳細を必要時だけ展開できることを目視確認する。
- Tailscale header/origin/source IPの組合せ、Funnel、Host偽装、public accessを自動testする。
- API response、HTML、JavaScript state、technical logにreporter情報がないことを検査する。
- 連打、戻る、reload、別tab、server再起動でもapprovalが一度だけ実行されることを確認する。

### 4.4 回帰test

最低限、次を通します。

```bash
git diff --check
.venv/bin/python -m unittest discover -s tests -p 'test_user_feedback*.py'
.venv/bin/python -m unittest tests.test_question_issue_reports
.venv/bin/python -m unittest discover -s tests -p 'test_question_review*.py'
.venv/bin/python -m unittest tests.test_documentation_structure
.venv/bin/python scripts/check/check_00_source_immutability.py
```

実装でmerge、projection、publisherを変更した場合は、その責務の既存testを追加実行し、変更理由と結果をreceiptへ残します。

### 4.5 Live-safe verification

- production Firestoreはintake schemaと件数をread-onlyで確認する。
- live questionへのwriteはfeedback systemから実行しない。
- Codex App Serverはread-only fixture caseでA/B/challengeと実model readbackを確認する。
- Gitは一時bare remote又はfixture repoでcommit/push/retryを検証する。
- スマホ実機はTailscale private HTTPSでfixtureだけを承認する。

## 5. 完了条件

次をすべて満たした場合だけ、実装を完了とします。

### Contract

- [ ] 目標正本、lane config、status schema、prompt fingerprintが実装と一致する。
- [ ] 現行manual batchから新systemへのmigrationとrollback条件が文書化されている。
- [ ] `00_source`、ID、許可field、一次根拠、法令三段階監査を弱めていない。

### Runtime

- [ ] `/Users/yuki/development/exam_scraper_feedback`がcleanな`main`で`origin/main`と一致する。
- [ ] `launchd`がログイン時起動、異常終了再起動、一時停止・再開に成功する。
- [ ] Codex App Server停止、Firestore切断、GitHub障害、Mac sleepからresumeできる。
- [ ] 外部AIへfallbackしない。

### AI and proposal

- [ ] AI-A/Bが別sessionでblindに動き、challenge前にreport claimを見ない。
- [ ] 修正根拠は一次情報だけで、根拠不足はholdになる。
- [ ] 最新repoとFirestoreのhash driftで旧proposalが失効する。
- [ ] fix cardは全事前gateとmerge preview成功後だけ表示される。

### Mobile approval

- [ ] Tailscale private HTTPSからスマホで一覧と集中モードを操作できる。
- [ ] 全ケースと修正候補のみを手動で切り替えられる。
- [ ] 要約、diff、一次根拠、correction unit、Firestore未反映が一度で理解できる。
- [ ] approve、no-change、hold、差し戻し、5秒undoが冪等に動く。
- [ ] passkey、Face ID、追加PIN、通知を要求しない。

### Patch and Git

- [ ] 未承認proposalがmergeへ入らない。
- [ ] 承認proposalと正式patchのhashが一致し、correction unitを原子的に保存する。
- [ ] 一件の正式patchだけを`main`へcommitし、`origin/main`へpushする。
- [ ] unrelated fileをstage、commit、rollbackしない。
- [ ] 同一内容のretryは承認を維持し、最大3回で停止する。
- [ ] 訂正は元履歴を削除せず新correction patchとして承認される。

### Publication and history

- [ ] feedback systemは承認時にFirestore questionへ書き込まない。
- [ ] 次回の既存mergeで正式patchが反映され、既存公開フローのreadback後だけ`published`になる。
- [ ] no-change、hold、app-update、published、訂正履歴を削除せず検索できる。
- [ ] 報告者へ結果を返さない。
- [ ] reporter情報、raw report、思考過程がproposal、Git、通常ログ、archiveへ漏れない。

### Full enable

- [ ] fixtureの全decision、障害、復旧、security caseが合格する。
- [ ] live read-only inventoryで未知schemaとidentity blockerがゼロである。
- [ ] 全資格・全報告を一度に有効化し、enable前後のcase数が収束する。
- [ ] スマホ実機、service、Firestore、Codex App Server、Gitのlive readbackが成功する。
- [ ] `output/user_feedback_response_system/installation/result.json`へscope、test、service、Tailscale、live readbackのreceiptを保存する。
- [ ] 実装変更を内容別にcommitし、`origin/main`へpushする。

## 6. Rollback

full enable後に重大不具合が見つかった場合は、`launchd` workerを停止してautomatic claimだけを無効にします。Firestore intake、未承認proposal、承認履歴、正式patch、Git commitは削除しません。Tailscale画面はread-onlyで履歴とhealthを確認できる状態を優先します。

既にcommitした正式patchを戻す必要がある場合は、元patch又は履歴を削除せず、正本仕様どおり打ち消すcorrection patchを別承認します。Firestoreに公開済みの内容は、既存問題公開フローの新しいmerge、write、readbackで訂正します。
