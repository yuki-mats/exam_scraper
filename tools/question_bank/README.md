# question_bank tools

過去問整備で迷ったら、まずこのディレクトリを見ます。

## 読む順番

1. ルール: `document/reference/question_field_contract.md`
2. 日々の目視作業: `prompt/README.md` と `prompt/01_prompt_*.md` から `prompt/04_prompt_*.md`
   - 03の前に `prompt/02b_prompt_prepare_law_context.md` で `18_law_context_prepared/` を作り、法令フラグと現行法根拠候補を `20_merged_1/` に反映してから解説を書く
   - 法改正・現行法差分が疑われる場合、年1回の法令関係問題の全問監査、または監査方式を更新した再監査では `prompt/03b_prompt_audit_current_law_and_patch.md` で03bの監査パッチ/sidecarを作成・更新し、既存成果物へマージする
3. 機械チェック: このディレクトリの `question_bank.py`
4. 補助実装: 必要な場合だけ `scripts/` 配下を見る

## 標準コマンド

日々の整備後は、個別 script を探す前にこのコマンドを実行します。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

既存互換のため、次の旧コマンドも同じ処理へ委譲します。

```bash
python scripts/check/run_question_quality_gate.py \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

## 何を確認するか

- `00_source`、merged、`40_convert` の必須フィールド
- `questionType`、`questionIntent`、`explanationText`、`questionSetId` patch の全問 coverage
- `18_law_context_prepared` を使う場合の法令コンテキスト coverage
- `suggestedQuestions` / `suggestedQuestionDetails` の整合
- `isLawRelated` の有無と `lawGroundedExplanationNotNeeded` との逆関係
- `lawReferences` の基本構造
- `lawRevisionFacts` の基本構造
- Firestore upload dry-run による schema validation

部分実行したい場合:

```bash
python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id> --mode required
python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id> --mode patches
python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id> --mode firestore
```

02bを標準工程として必須にする場合は、`--require-law-context-stage` を追加します。03工程後は、全解説 patch に厳密な `isLawRelated` と、互換フラグとしての `lawGroundedExplanationNotNeeded` を必ず残すため、次を追加します。AI解説・条文確認の正本は、03bで確定した `lawRevisionFacts` を使います。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id> \
  --require-law-context-stage \
  --require-is-law-related \
  --require-law-grounded-flag \
  --require-law-revision-facts \
  --require-law-evidence-utilization \
  --require-law-references-for-law-related
```

`--require-law-revision-facts` を付けた quality-gate は、解説 patch だけでなく、選択した mode に応じて `30_merged_2/` または `40_convert/` の実データ上でも `isLawRelated=true` 全件に `lawRevisionFacts` があるか確認します。`--require-law-references-for-law-related` は、法令関連レコードに `lawReferences` が空で残る状態を検出します。最終公開前はさらに `--fail-on-law-revision-hold` と `--require-law-revision-evidence-summary` を追加し、`hold` や根拠要約不足を残さない状態を目標にします。`updated_to_current_law` の正答更新は、原則として `reviewState=tertiary_verified` になってから公開確定します。

`--require-law-evidence-utilization` は、法令関連の `21_explanationText_added` について、既存の `lawRevisionFacts` / `lawReferences` が `explanationText`、想定質問チップ、保存済み回答に最低限反映されているかを機械チェックします。大きな別レビュー工程は増やさず、汎用チップだけで法令問題を通してしまう事故を防ぐための軽い gate です。

e-Gov API v2 / 整備済み corpus に出題当時 revision が保持されていない問題は、資格別方針で認める場合、現行法ベースのみで確定してよい。この場合は `lawRevisionFacts.examTime.correctChoiceText` に公式元正答を残し、`examTime.verificationStatus="not_referenced_current_law_only_policy"` または `"from_original_answer"`、`notes` / review sidecar の `remainingRisk` に未参照理由を保存します。出題当時 revision がないこと自体は `hold` 理由にせず、現行法根拠・委任規定・施行令・施行規則・告示・別表等が不足する場合だけ `hold` に戻します。

現時点の未整備数を棚卸しするだけなら、単体 checker で report を残します。

```bash
python tools/question_bank/question_bank.py check-law-revision-facts \
  --list-group-dir output/<qualification>/questions_json/<list_group_id> \
  --stage firestore \
  --report output/<qualification>/review/law_revision_audit/<list_group_id>_law_revision_fact_coverage_<timestamp>.json
```

## 公式問題の問題報告

Repaso から届いた問題報告は定期 polling せず、依頼された時だけ次で棚卸しします。

```bash
python tools/question_bank/question_bank.py report-inventory \
  --credentials-json /secure/service-account.json
```

カテゴリを選んだら棚卸し時点の対象を固定し、総量への承認を一度だけ得ます。

```bash
python tools/question_bank/question_bank.py report-snapshot \
  --category correct_answer \
  --credentials-json /secure/service-account.json \
  --output output/question_issue_reports/batches/correct-answer.json

python tools/question_bank/question_bank.py report-run \
  --manifest output/question_issue_reports/batches/correct-answer.json \
  --approve --execute-publish \
  --credentials-json /secure/service-account.json
```

`report-run` は blind A/B → challenge → `24_questionIssueCorrections` → merge → gate → correction unit 対象 docs だけの不変 artifact で upload dry-run → commit/push →同じ artifact の upload → live readback を順に実行します。raw comment を blind review、patch、Git、通常ログへ入れません。詳細は `document/operations/question_issue_report_workflow.md` を正本にします。

production では `--approve --execute-publish` を組にし、承認後に patch preview だけで止まる実行と current 値への no-op patch を拒否します。commit 後の push/upload/readback 失敗は `publish_pending` として保持され、hash 固定した単一 correction unit artifact と同じ commit を次で再試行します。remote がその commit をすでに含む場合は再 push を省略し、未反映時も fast-forward だけを許可します。

```bash
python tools/question_bank/question_bank.py report-retry-publish \
  --credentials-json /secure/service-account.json
```

報告由来 overlay 単体の確認:

```bash
python tools/question_bank/question_bank.py check-question-issue-correction \
  --patch output/<qualification>/questions_json/<list_group_id>/24_questionIssueCorrections/<patch>.json
```

法令本文の再現性を確保する場合は、Firestore 変換後の verified `lawReferences` から e-Gov 条文スナップショットを取得します。これはアプリ実行時の検索ではなく、問題整備時の evidence 蓄積です。

```bash
python scripts/pipeline/fetch_law_article_snapshots.py \
  --list-group-dir output/<qualification>/questions_json/<list_group_id> \
  --timestamp <YYYYMMDD_HHMM> \
  --fail-on-fetch-error
```

出力先は `output/<qualification>/law_evidence/<list_group_id>/current_article_snapshots/` です。JSONL には `articleText`、`articleTextHash`、`rawXmlHash`、`apiUrl`、紐づく `questionIds` を保存し、raw XML は `raw_xml/<timestamp>/` に保存します。

未整備の法令関連問題を監査対象として切り出す場合は、取得済み snapshot と照合した JSONL queue を作ります。これは `same_as_current` / `updated_to_current_law` / `hold` を自動断定する工程ではなく、監査者またはAI補助が同じ根拠から判断できるように、対象問題・現行正誤・lawReferences・条文 hash/API URL を束ねる工程です。この固定入力から `auditInputHash` を作り、一次監査・二次監査・三次確定で同じ evidence bundle を参照します。

```bash
python tools/question_bank/question_bank.py build-law-revision-audit-queue \
  --list-group-dir output/<qualification>/questions_json/<list_group_id> \
  --snapshots output/<qualification>/law_evidence/<list_group_id>/current_article_snapshots/<list_group_id>_current_article_snapshots_<timestamp>.jsonl \
  --output output/<qualification>/review/law_revision_audit/<list_group_id>_law_revision_audit_queue_<timestamp>.jsonl \
  --summary output/<qualification>/review/law_revision_audit/<list_group_id>_law_revision_audit_queue_<timestamp>_summary.json \
  --require-snapshots
```

queue の各行は `auditReason=missing_lawRevisionFacts` または `hold` を持ち、`currentEvidence.refs[].snapshot.articleTextHash` と raw XML の hash を含みます。同一 `originalQuestionId` の派生レコードに `lawReferences` が空で、兄弟レコードに根拠がある場合は `lawReferencesSource=same_original_question_fallback` として明示します。これは根拠欠落を隠すためではなく、監査 queue 上で根拠候補を失わないためです。最終公開前は、この queue を消化して `lawRevisionFacts` を作成し、`check-law-revision-facts --require-all-law-related` を通します。

queue 作成直後にまだ出題当時法令との差分を確定できない場合は、`hold` の `lawRevisionFacts` を 21系 explanation patch に初期化します。これは正答変更を断定する工程ではありません。現行法の locator / hash / snapshot、`auditedAt`、`auditMethodVersion`、`auditInputHash`、`lawCorpusSnapshotId` を保存し、`reviewState=needs_secondary_review` として自由質問 AI に「推測して断定しない」前提を渡すための工程です。

```bash
python tools/question_bank/question_bank.py materialize-law-revision-hold-facts \
  --queue-jsonl output/<qualification>/review/law_revision_audit/<list_group_id>_law_revision_audit_queue_<timestamp>.jsonl \
  --explanation-patch output/<qualification>/questions_json/<list_group_id>/21_explanationText_added/<patch>.json \
  --output output/<qualification>/questions_json/<list_group_id>/21_explanationText_added/<patch>_hold_facts_<timestamp>.json \
  --skip-missing-patch-ids
```

この `hold` は公開前の最終状態ではなく、二次監査または三次確定の対象です。二次監査では一次で取得した evidence bundle を使って妥当性を確認し、`same_as_current` / `not_law_related` は `secondary_verified` で確定可とします。`updated_to_current_law`、一次/二次不一致、高リスク判断は三次確定で `tertiary_verified` にしてから正答・解説更新を公開確定します。最終公開前は `--fail-on-law-revision-hold` と `--require-law-revision-evidence-summary` を付けた gate を通します。

root 直下に出てしまった資格別レポートは、資格フォルダ配下へ寄せます。

```bash
python tools/question_bank/question_bank.py organize-reports --qualification gas-shunin
```

## 作業中の単体確認

全体ゲート前に、raw JSON の正式 patch 化や作成中の patch だけ確認したい場合も、このCLIから実行します。

```bash
python tools/question_bank/question_bank.py materialize-patch \
  --task question_type \
  --source /path/to/question_*.json \
  --raw /path/to/raw.json \
  --output /path/to/10_questionType_fixed/question_*_questionType_fixed.json
```

```bash
python tools/question_bank/question_bank.py check-question-type-patch \
  --source /path/to/question_*.json \
  --patch /path/to/10_questionType_fixed/question_*_questionType_fixed.json
```

```bash
python tools/question_bank/question_bank.py check-question-intent-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/15_correctChoiceText_fixed/question_*_correctChoiceText_fixed.json
```

```bash
python tools/question_bank/question_bank.py check-law-context-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/18_law_context_prepared/question_*_merged_lawContext_prepared.json
```

```bash
python tools/question_bank/question_bank.py check-explanation-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/21_explanationText_added/question_*_explanationText_added.json \
  --require-is-law-related \
  --require-law-grounded-flag \
  --require-law-revision-facts
```

```bash
python tools/question_bank/question_bank.py check-question-set-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/22_questionSetId_linked/question_*_questionSetId_linked.json \
  --category /path/to/category.json \
  --questionset-only
```

## フォルダ責務

| 場所 | 役割 |
| --- | --- |
| `document/reference/question_field_contract.md` | 共通フィールドの人間向け正本。 |
| `prompt/` | 01から04の目視 patch 作成プロンプト。02bで法令コンテキスト、03で解説本文を作る。`question_issue_reports/` は blind A/B と challenge の共通 orchestrator。 |
| `tools/question_bank/` | 日常運用で直接叩く統一CLI。 |
| `scripts/` | CLIから呼ばれる実装、互換入口、個別補助。通常は直接探さない。 |
| `output/` | 資格ごとの生成物・作業中データ。Git管理の正本にしない。root直下に単発レポートを増やさず、`output/<qualification>/reports/` へ置く。 |

## Codex が改修するときのルール

- 日常運用の入口を増やす場合は、まず `question_bank.py` のサブコマンドとして追加する。
- 個別 checker / fixer を追加する場合も、ユーザー向けREADMEでは `tools/question_bank` からたどれるようにする。
- 新しい監査・修復レポートの既定出力先は `output/<qualification>/reports/` にする。既存のroot直下レポートは `organize-reports` で移す。
- `prompt/`、field contract、merge/convert/upload の仕様を変えたら、このREADMEと `quality-gate` の対象も同じ commit で見直す。
- `scripts/` に新しい単発スクリプトを置く場合は、日常運用の正本にするのか、内部補助に留めるのかを `scripts/README.md` に明記する。
