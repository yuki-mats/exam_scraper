# question_bank CLI

`tools/question_bank/question_bank.py`は、問題整備の検証・レビュー・補助処理をまとめた日常CLIです。この文書は**コマンド入口だけ**を管理します。工程は[問題整備ワークフロー](../../document/operations/exam_pipeline_manual_and_automation.md)、判断方法は[prompt一覧](../../prompt/README.md)を参照してください。

## 標準品質ゲート

```bash
python3 tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

部分確認は`--mode required`、`--mode patches`、`--mode firestore`を使います。法令工程を必須にする場合は、必要に応じて次を追加します。

```text
--require-law-context-stage
--require-is-law-related
--require-law-grounded-flag
--require-law-revision-facts
--require-law-evidence-utilization
--require-law-references-for-law-related
--fail-on-law-revision-hold
--require-law-revision-evidence-summary
```

optionの正確な組合せは`quality-gate --help`を確認します。既存互換の`scripts/check/run_question_quality_gate.py`はこの入口へ委譲します。

## レビューUI

```bash
python3 tools/question_bank/question_bank.py review-ui
```

初期表示を指定する場合:

```bash
python3 tools/question_bank/question_bank.py review-ui \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

UIの安全境界と保存先は[レビューコンソール仕様](../../document/operations/local_question_review_console.md)が正本です。

## 公開済み問題の初期版付与

最初に本番Firestoreを読み取り、公開対象とローカル問題の全件一致を確認します。この時点では書き込みません。

```bash
python3 tools/question_bank/question_bank.py backfill-work-versions
```

`unmatchedQuestionCount=0`を確認後、公開済みだが使用版を証明できない各問題へlegacy `v0`をローカルに記録します。

```bash
python3 tools/question_bank/question_bank.py backfill-work-versions --execute
```

このコマンドはFirestoreを変更せず、既存の検証済み工程版も上書きしません。結果は`output/question_review_console/work_version_backfills/<timestamp>/manifest.json`へ保存します。版の意味と洗い替え方法は[作業バージョン](../../document/operations/local_question_review_console.md#作業バージョン)を参照してください。

## patch単体

最小JSONを正式patchへ変換:

```bash
python3 tools/question_bank/question_bank.py materialize-patch \
  --task <question_type|question_intent|correct_choice|law_context|explanation|question_set> \
  --source <source.json> --raw <raw.json> --output <patch.json>
```

単体checker:

```text
check-question-type-patch
check-question-intent-patch
check-law-context-patch
check-explanation-patch
check-question-set-patch
check-question-issue-correction
```

各引数は`<subcommand> --help`を正本とし、この文書へ複製しません。

## 法令監査補助

```text
check-law-revision-facts
build-law-revision-audit-queue
materialize-law-revision-hold-facts
```

これらはevidence取得や監査判断そのものを自動化しません。監査契約は[現行法監査](../../document/operations/current_law_question_maintenance_workflow.md)を参照してください。

## 公式問題報告

```text
report-inventory
report-snapshot
report-run
report-retry-publish
```

blind review、承認、公開範囲は[問題報告workflow](../../document/operations/question_issue_report_workflow.md)が正本です。

## report整理

```bash
python3 tools/question_bank/question_bank.py organize-reports \
  --qualification <qualification>
```

再生成可能なreportは`output/<qualification>/reports/`へ置き、repository文書へ混ぜません。

## CLI変更ルール

- 日常入口を増やす場合は、まずこのCLIのsubcommandとして追加する。
- 個別scriptを追加しても、通常利用者にはこのCLIから到達させる。
- subcommand追加時は`--help`、テスト、この文書の一覧を同じcommitで更新する。
- workflowやfield仕様はこの文書へ書かず、それぞれの正本を更新する。
