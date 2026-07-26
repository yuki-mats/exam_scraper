# T002 Judge decision

## Decision

`approved`

T001の案を次の条件で承認する。

1. Job lockは階層化する。異なる資格のmaintenance key同士だけを同時許可し、repository-wide keyは全資格maintenance keyと衝突させる。
2. 全体32turnは`CodexAppServerClient`が所有する公平budgetで保証する。qualificationをgroupとしてrun期間中registerし、各turnはslot取得後だけApp Serverへ送る。
3. `questionConcurrency=32`はrun内の希望上限であり、実in-flightはglobal budgetとadaptive schedulerの小さい方とする。
4. Fastはrunの`speedMode`に保存し、thread/startとturn/startの両方で`fast`を要求する。responseが一致しない場合は失敗する。
5. Standardの追加credit不使用gateとFastのcredit必須gateを分ける。黙示fallbackは禁止する。
6. public statusとrun progressへglobal budget、requested speed、actual service tierを出し、live readback可能にする。
7. UI初期値はStandard、concurrency初期値は32とする。32からの自動縮小はログとmanifestへ残す。
8. 既存runのresume selection、validated work、単一資格時の動作を回帰testで守る。

## Worker package

### Objective

階層job lock、global fair turn budget 32、run単位Standard/Fast、UI/API/run telemetryを一つのvertical sliceとして実装する。

### allowed_files

- `tools/question_review_console/jobs.py`
- `tools/question_review_console/turn_budget.py`
- `tools/question_review_console/codex_app_server.py`
- `tools/question_review_console/qualification_runs.py`
- `tools/question_review_console/server.py`
- `tools/question_review_console/static/index.html`
- `tools/question_review_console/static/app.js`
- `tools/question_review_console/static/styles.css`
- `document/operations/local_question_review_console.md`
- `tests/test_question_review_jobs.py`
- `tests/test_question_review_turn_budget.py`
- `tests/test_question_review_codex_app_server.py`
- `tests/test_question_review_qualification_runs.py`
- `tests/test_question_review_server.py`
- `tests/test_question_review_workflow.py`
- `docs/goals/question-maintenance-multi-qualification-fast/**`

### verify

- `.venv/bin/python -m unittest tests.test_question_review_jobs tests.test_question_review_turn_budget tests.test_question_review_codex_app_server tests.test_question_review_qualification_runs tests.test_question_review_server tests.test_question_review_workflow`
- `node --check tools/question_review_console/static/app.js`
- `.venv/bin/python -m compileall tools/question_review_console`
- `git diff --check`
- `.venv/bin/python scripts/check/check_00_source_immutability.py`
- GoalBuddy state checker

### stop_if

- Fastのrequest/response service tierを確認できない。
- global 32turnをslot取得なしに通る経路が残る。
- repository-wide処理とmaintenanceの相互排他を維持できない。
- 同一資格へ複数writerが入る。
- 既存run resume又はvalidated workが失われる。
