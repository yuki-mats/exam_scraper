# T001 現行システムmap

## 結論

- maintenance jobは`REPOSITORY_OPERATION_KEY`を共有しているため、資格が異なっても同時開始できない。
- `normalize_question_concurrency()`は`1 / 5 / 10 / 32`を受理するが、新規runでは常に`1`を返す。
- `AdaptiveLimits`とbatch executorは32turnまでの並列構造を既に持つ。
- 1台の`CodexAppServerClient`はrequest ID、status refresh、turn stateをlockで保護しており、複数top-level turnを扱える。subagent上限1はtop-level turnのglobal上限ではない。
- Fastは`features.fast_mode=false`、`serviceTier=None`でthread/startとturn/startの両方に固定され、subscription gateもcredits有効accountを拒否する。
- CLF-C02のcanonical qualificationは`aws-cloud-practitioner`。`ping-t-aws-clf-c02`に547問、`keepitup-aws-clf-c02`に332問あり、資格rulesで法令工程は無効。
- 中断した`gas-shunin-otsu` run `20260726T083408432999-ac9ebd8c`は`retrySafe=true`、検証済み95作業。確定済み01差分2fileはcoverageと`00_source`不変を検証し、commit `ed109504a`として`origin/main`へpush済み。

## 実装境界

1. maintenance jobだけを資格別operation keyへ変更する。sync、direct edit、merge、upload、Git相当のrepository-wide処理はglobal keyを維持する。
2. `CodexAppServerClient`へ全体hard cap 32の公平turn budgetを持たせる。各maintenance turnはqualificationをgroup keyとして取得・解放し、status APIへin-flight、waiting、peak、group別割当を返す。
3. 各runの`questionConcurrency`は最大32を復活させるが、App Server直前のglobal budgetで全資格合計32を機械保証する。
4. run単位に`speedMode=standard|fast`を保存する。Fastはthread/startとturn/startで`serviceTier=fast`、`features.fast_mode=true`を要求し、実応答がFastでなければ失敗する。
5. subscription gateはStandardとFastを分ける。Fastはcreditsを要求し、利用不能時は開始前に明示エラーとする。Standardへの黙示fallbackは禁止する。
6. UIはStandardを初期値とし、明示選択でFastにする。preview、start、run表示、session statusへspeed modeとturn budgetを反映する。

## Worker package

### allowed files

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

- `python -m unittest tests.test_question_review_turn_budget tests.test_question_review_codex_app_server tests.test_question_review_qualification_runs tests.test_question_review_server tests.test_question_review_workflow`
- `node --check tools/question_review_console/static/app.js`
- `python -m compileall tools/question_review_console`
- `git diff --check`
- `.venv/bin/python scripts/check/check_00_source_immutability.py`
- GoalBuddy board checker

### stop conditions

- App ServerがFast service tierをrequest/responseで確認できない。
- 同じ資格への複数writer又は異なる資格の同一canonical file書込みが生じる。
- global turn capをApp Server直前で機械保証できない。
- 既存runのresume selection又はvalidated workを失う。
