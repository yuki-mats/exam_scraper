# T046 deterministic per-question pipeline map

## 現行責務

1. `prepare_spec`が現在projection、対象工程、source identity、更新先recordの一意性を確認する。
2. `prepare_batch`が一問だけのprompt、typed candidate契約、attempt、入力fingerprintを作る。
3. `run_model`がread-only model turnを実行し、candidate v3をattemptへwrite-onceで保存する。
4. `run_commit`が保存済みcandidateを再読し、server検証、patch反映、作業版、checkpointを確定する。
5. coordinatorが一問stateから親集計を更新し、次工程又は再試行へ送る。

`prepare_spec`と`prepare_batch`は`question-preparation` executor、`run_model`は`question-model` executor、`run_commit`は`question-commit` executorと別queueで動く。業務上必要なのは一問の状態遷移であり、preparation worker又はcommit writerという独立した判断主体ではない。

## 既存の決定的tool

`IsolatedQuestionPatchWorkspace`は既に次を実装している。

- 完全なsource identityによる対象record照合
- legacy aliasの誤配送防止
- 一問の候補recordだけを最新canonical siblingへrebase
- 実patch pathを安定順に取得するprocess間file lock
- 同一recordの同時変更をfail-closedで検出
- atomic replaceと部分失敗の明示

したがって、新しいpatch形式又は別updaterを増やす必要はない。

## 共有mutable resource

| resource | 現行scope | あるべきscope |
| --- | --- | --- |
| 工程・年度単位patch JSON/JSONL | 実path lockに加えて資格・年度lock | 実path lockだけ |
| `work_versions.json` | 資格・年度lockとstore内lock | patchと同じ実path transactionへ追加 |
| 一問state / attempt | 問題path lock | 維持 |
| attempt baseline / result / progress | attempt固有path | 維持 |
| parent manifest / summary | coordinator集約 | 維持 |
| inventory cache | patch lock内でinvalidate | 確定後にlock外でinvalidate |

現行の資格・年度lock内にはbaseline、run state、record scope検証、`00_source`検査、patch commit、inventory invalidate、work version、checkpoint、baseline削除が混在する。実path transactionに必要なのは、競合再確認、patchとwork-versionの更新、確定checkpoint、失敗時rollbackである。

## 最小のtarget architecture

```text
question state machine (max 100)
  -> deterministic input tool
  -> read-only model turn
  -> deterministic candidate validation
  -> deterministic patch apply tool
  -> immutable per-question receipt
  -> next stage
```

- model executorは意味判断と文章生成だけを担当する。
- 入力生成とpatch反映は一つの汎用tool executorで動かし、専用worker名・専用poolを持たない。
- attemptへ保存したcandidateとfingerprintを境界とし、既存resumeを維持する。
- patch applyは候補patch pathと対象`work_versions.json`を同じ安定順file lockへ含める。
- 新規runでは`patchApplyStartedAt`、`patchToolQueueWaitSeconds`、`patchToolLockWaitSeconds`、親`patchTools`を記録し、旧`candidateCommitStartedAt`はresume判定のread-only互換として読む。
- patch形式、work-version形式、既存run artifactは移行書換えしない。

## 実装候補file

- `tools/question_review_console/qualification_runs.py`
- `tools/question_review_console/question_patch_proposal.py`
- `tests/test_question_review_qualification_runs.py`
- `tests/test_question_review_question_patch_proposal.py`
- `tests/test_question_review_question_run_state.py`
- `document/operations/local_question_review_console.md`
- `document/operations/artifact_contract.md`

## 必須検証

- 100問が一問一turnを維持する。
- 入力toolとpatch toolが同じ汎用executorを使い、専用preparation/commit executorがない。
- 異なる実pathは同時適用でき、同じ実pathは直列化される。
- 同じrecordの競合は上書きせず停止する。
- 一問が複数patch pathを更新する場合は全pathを安定順にlockする。
- `work_versions.json`を含む途中失敗は開始前へrollbackする。
- model完了後の候補はattemptへ永続化され、旧run resumeも再modelなしで確定できる。
- `00_source`、既存ID、patch形式、既存artifactを変更しない。

## Baseline

次の現行回帰は変更前に成功した。

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_question_review_question_patch_proposal \
  tests.test_question_review_question_candidate \
  tests.test_question_review_question_run_state \
  tests.test_question_review_work_versions \
  tests.test_question_review_qualification_runs
```
