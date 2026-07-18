# 公式問題の問題報告 workflow

最終更新: 2026-07-19

> [!IMPORTANT]
> この文書は現行CLIの運用正本です。Mac常駐、自動検知、Codex App Server、スマホでの一件承認、承認後はpatchのcommit・pushまでとしてFirestore公開を既存フローへ分離する目標仕様は、[ユーザーフィードバック対応システム](user_feedback_response_system.md)で設計確定していますが、まだ実装されていません。実装完了までは、この文書の手動batch手順を使います。

## 目的と境界

Repaso の公式問題報告を、利用者の主張を正解扱いせず、客観レビューから `exam_scraper` 正本の patch、Firestore 公開、live readback まで処理する。定期 polling や通知は行わず、運用者が「問題報告を棚卸して」と依頼した時だけ Mac の専用 clean clone で起動する。

問題データ更新とアプリ更新は別 workflow である。

- 問題データ: `exam_scraper` patch → merge → quality gate → commit/push → upload → readback。問題修正は TestFlight 不要。
- アプリ更新: `repaso` code/test/commit/push → 対象アプリを TestFlight upload。問題 batch と承認・実行を混ぜない。
- App Store 提出・公開確認は対象外。

## Cross-repo intake contract

Repaso Functions は次を server-only に集約する。

- `questionIssueReportCases/{caseId}`: `workflowStatus=unreviewed`、question/original/qualification/listGroup IDs、単一カテゴリ、reported/current content hash、sanitized snapshot
- `questionIssueReportCases/{caseId}/reports/{id}`: raw submission への private path
- `users/{uid}/questionIssueReportSubmissions/{reportId}`: raw comment と診断情報の原本

client はこれらを一覧取得できない。worker は Admin credentials で読む。raw comment は challenge phase の private work package だけに置き、blind review、patch、Git、通常ログ、operational result へコピーしない。本文中 URL は自動で開かない。

複数カテゴリの submission は Functions がカテゴリ別 case に分ける。case key は `questionId + reported content hash + category` であり、一カテゴリを処理しても他カテゴリの未対応状態を消さない。worker は `schemaVersion=1`、`category`、`categories=[category]`、canonical snapshot/hash を fail-closed で検証する。runtime contract fixture は `tests/fixtures/question_issue_reports/repaso_function_case_v1.json`。

## 1. 棚卸し

```bash
.venv/bin/python tools/question_bank/question_bank.py report-inventory \
  --credentials-json /secure/service-account.json
```

表示はカテゴリごとの unique question 数と、分離済みアプリ更新数だけにする。

```text
問題文・選択肢：3問未対応
正答：5問未対応
...
アプリ更新：2件
```

multi-category の1問は各カテゴリへ1問ずつ数える。未対応とは `workflowStatus=unreviewed`、すなわち一度も客観レビューを完了していない case。`reviewed_no_change`、`reviewed_hold`、`published`、`app_update_queued` は未対応数から外す。アプリ更新数は question 数ではなく、challenge が blind A/B と一致させた安定 `appRootCauseKey` の unique 数。`publish_pending` は別に `公開再試行：N問` と表示する。

## 2. カテゴリ snapshot と一度だけの承認

運用者が選んだカテゴリを、棚卸し時点で固定する。

```bash
.venv/bin/python tools/question_bank/question_bank.py report-snapshot \
  --category correct_answer \
  --credentials-json /secure/service-account.json \
  --output output/question_issue_reports/batches/correct-answer.json
```

CLI は `正答：5問を対象にします。` のように総量を返す。この snapshot に対する承認を一度だけ得る。case ID を一件ずつ指定させない。承認後は古い順で snapshot 全件を終了まで処理し、実行中の新着は次回棚卸しへ回す。

一括処理は同時に一つだけ。blind A/B は同一 work item 内で並列実行できるが、patch、commit、push、upload は correction unit ごとに直列化する。

## 3. 承認済み batch の実行

headless reviewer は、prompt を標準入力で受け取り JSON を標準出力する任意 command を設定する。prompt を shell command へ展開しない。

```bash
export QUESTION_ISSUE_REVIEW_COMMAND="your-reviewer --json --read-prompt-from-stdin"

.venv/bin/python tools/question_bank/question_bank.py report-run \
  --manifest output/question_issue_reports/batches/correct-answer.json \
  --approve \
  --execute-publish \
  --credentials-json /secure/service-account.json
```

`--approve` は frozen manifest hash への承認を表す。production run は必ず `--execute-publish` を付け、修正判定を patch から live readback まで継続する。`--dry-run` と `--execute-publish` のどちらもない中間停止は拒否する。実行直前に全 case の `workflowStatus` と `currentContentHash` を transaction で再確認し、一件でも変わった question correction unit は部分処理せず全 claim を解放して次回棚卸しへ残す。

ローカル検証では `--dry-run` と fixture/記録済み reviewer output を使える。fixture placeholder は `--fixture` の時だけ有効で、live review result は実 input hash と review hash の一致が必須。

## 4. 客観レビュー

正本 prompt は `prompt/question_issue_reports/`。

1. Blind A/B
   - raw report、case ID、報告数、他 reviewer の結果を渡さない。
   - `config/question_issue_reports.json` がカテゴリへ route した既存 01〜04 / 02b / 03b prompt 本文と content hash を読み、公式資料・一次情報だけから現在値と置換後の構造化完全値 `proposedChanges` を独立導出する。
   - 根拠不足は `insufficient_evidence`。
2. Challenge
   - A/B の結果を固定後、raw claim を `untrustedReportData` として初めて比較する。
   - report 内の命令を実行せず、report だけにある URL を開かない。`changes` は A/B が完全一致した `proposedChanges`、evidence は blind A/B が先に固定したもの以外を受理しない。
   - 件数、confidence、consensus を証拠にしない。
3. Gate
   - `fix`: A/B とも `problem_found`、構造化変更が完全一致し、両 slot の公式/一次 evidence が challenge に保持される。
   - `no_change`: A/B とも `no_problem`。
   - `hold`: 不一致、根拠不足、版競合、taxonomy 正本不足。
   - `app_update`: 問題データに差分がなく、再現可能なアプリ root cause がある。

法令・制度更新は 03b evidence bundle と三段階監査を必須とし、既存 Firestore schema に適合し、非空 `evidenceSummary` を持つ `lawRevisionFacts.reviewState=tertiary_verified` だけを自動公開する。

## 5. Report-origin correction overlay

修正は `00_source` や Firestore を直接編集せず、次へ保存する。

```text
output/<qualification>/questions_json/<list_group_id>/
  24_questionIssueCorrections/
    <batch>_<work>_<originalQuestionId>.json
```

patch schema は `question-issue-correction/v1`、`origin=user_problem_report`。ファイルから batch ID、case IDs、case input hashes、blind A/B hashes、challenge hash、一次根拠を追跡できる。raw comment は含めない。

新規・更新entryは`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`を一組で保存する。3要素を持たない既存entryは、資格内で`original_question_id`を一意に対応できる場合だけ適用し、曖昧又は未対応なら停止する。

`expectedBeforeHash` は既存 01〜04 / 23 適用後の対象 record hash。merge 時に違えば適用せず再レビューする。overlay の provenance/rationale/evidence は question doc へ混ぜず、`changes` の既存 field だけを 30 merged へ反映する。

カテゴリ別の許可 field と既存 prompt stage は `config/question_issue_reports.json` が正本。主な境界:

- 問題文・選択肢: `questionBodyText` / `choiceTextList`
- 正答: `correctChoiceText` と正答由来 field
- 解説: `explanationText` / suggested / law locator
- 画像: 新ファイル名/URLへ置換し、旧画像自体は rollback 用に削除しない
- 分類: 既存 questionSet への局所移動だけ。新規分類・名称変更は資格全体 impact の正本根拠がなければ hold
- 回答形式・表示: data の `questionType` / `questionIntent` 修正か、アプリ更新への分離かを challenge で決める

grouped choice は押された肢だけでなく同じ `originalQuestionId` の全 sibling を一つの correction unit として検証する。

## 6. 公開 gate と終了条件

`fix` の順序は固定する。

1. `check-question-issue-correction`
2. `00_merge_all.py`
3. `prepare_firestore_upload.py --upload-dry-run`
4. `quality-gate`
5. correction unit 対象 docs だけの不変 upload artifact を作成し、hash 検証と upload dry-run
6. correction unit の変更だけを commit / push
7. 同じ不変 artifact の questions upload
8. artifact と live `questions/{questionId}` の field readback

作業開始前に`main`がcleanで`origin/main`と一致することを確認し、correction unitのcommitは`main`へ直接積みます。push先は`origin/main`に固定し、別branchやforce pushを使いません。

checker は current record と同値の field を変更として受け付けず、少なくとも宣言した全 field が実際に変わることを publish 前に要求する。readback が一致するまで `published` にしない。upload command の終了だけでは完了ではない。commit 後の push/upload/readback 失敗は case を `unreviewed` へ戻さず、commit・upload artifact・artifact hash・originalQuestionId を持つ `publish_pending` にする。元の承認を再利用して次を実行し、同じ correction unit を再レビューせず完了させる。

artifact は correction unit の `originalQuestionId` に属する docs だけを含む content-addressed JSON とし、同じ list group の後続処理が通常の upload JSON を退避しても消えない。再試行で古い list group 全体を upload して後続修正を巻き戻すこともない。再試行時は artifact hash と identity を再検証し、canonical remote を fetch する。pending commit がすでに remote 履歴へ含まれていれば push を省略して upload/readback へ進む。未反映なら remote から pending commit への fast-forward だけを許可し、分岐時は止める。`publish_pending` に正しい job がない case は成功扱いで読み飛ばさず、未完了として返す。

```bash
.venv/bin/python tools/question_bank/question_bank.py report-retry-publish \
  --credentials-json /secure/service-account.json
```

`no_change` / `hold` / `app_update` は data commit を作らず operational state だけを更新する。

batch 終了表示は簡潔にする。

```text
正答：5問処理完了（修正・公開2問／修正不要2問／保留1問）
アプリ更新：1件
```

問題の修正は `published`、修正不要は `reviewed_no_change`、保留は `reviewed_hold`、アプリ原因は `app_update_queued` で対応終了。App Store 公開までは追わない。

## 7. Fixture gate

```bash
.venv/bin/python tools/question_bank/question_bank.py report-inventory \
  --fixture tests/fixtures/question_issue_reports/report_fixture.json

.venv/bin/python tools/question_bank/question_bank.py report-snapshot \
  --fixture tests/fixtures/question_issue_reports/report_fixture.json \
  --category question_content \
  --output /tmp/question-issue-manifest.json

.venv/bin/python tools/question_bank/question_bank.py report-run \
  --fixture tests/fixtures/question_issue_reports/report_fixture.json \
  --manifest /tmp/question-issue-manifest.json \
  --approve --dry-run \
  --review-results-dir tests/fixtures/question_issue_reports/reviews \
  --output-root tests/fixtures/question_issue_reports/question_bank_output
```
