# T001 現行境界の調査

## 結論

現行実装は、対象抽出、進捗イベント、工程版の保存単位には問題単位の情報を持つ。一方、成功判定、完了receipt、品質検証、baseline、rollback、再起動回収はhuman run全体を単位にする。この境界差が、一問の失敗で同じrunの他問まで未承認又はrollback対象になる直接原因である。

## 現在の境界

- 開始範囲は既に資格と`listGroupIds`で指定できる。`qualification_workflow.py:492-550`で年度・回相当のgroupを絞り、`735-868`で問題ごとのtargetとscopeをmanifestへ渡す。
- `progress.jsonl`は`question_started -> stage_completed -> question_completed`を問題ごとに順序検証する（`qualification_runs.py:1390-1443`）。
- ただし、問題を検証済みとみなす条件はmanifest全体の`status=succeeded`かつ`receiptValidated=true`である（`qualification_runs.py:1503-1515`）。一問だけを先に確定する状態はない。
- 最終進捗検証は、run全体の処理問題数・工程数が予定数と完全一致することを求める（`qualification_runs.py:4564-4589`）。一問不足するとrun全体が失敗する。
- 工程版は問題ごとに`policyFingerprint`と`runId`を保存できる（`work_versions.py:293-399`）。しかし記録はrun全体のreceipt検証後にまとめて行い、解説・法令監査も選択された全問題をまとめて品質検証する（`qualification_runs.py:4591-4686`）。
- human runは対象write範囲全体のbaselineを持つ。未検証状態で失敗すると`rollback_baseline()`を呼び、run全体を`failed`又は`interrupted`にする（`qualification_runs.py:4415-4532`）。
- server再起動時も、未検証human runはrun全体のbaselineを復元して失敗扱いにする（`qualification_runs.py:1776-1868`）。検証済みpatchと`artifactSync`失敗だけは分離して保持する。
- top maintenanceはphaseを直列実行し、child runが一つ失敗すると例外で後続へ進まない。`tests/test_question_review_qualification_flow_recovery.py:614-663`がこの停止を期待値として固定している。
- Merge・Convert・upload-readyは`receiptValidated=true`後にgroup単位でまとめて同期し、失敗時もpatch成功を保持する（`qualification_runs.py:4344-4435`）。この分離は維持できる。

## 変更時に守る境界

1. 親runは資格・年度・回のscopeと集計だけを所有する。
2. 各問題に永続work-itemを持たせ、対象工程、入力・方針fingerprint、状態、試行回数、停止理由、確定receiptを保存する。
3. 検証・工程版記録・rollbackをwork-itemへ縮小し、成功済みwork-itemを親run失敗から独立させる。
4. 一問が`blocked`でも次の独立問題を処理する。依存する後続工程だけを止める。
5. 同じfingerprintの`validated`問題は再実行しない。fingerprintが変わった問題だけを`queued`へ戻す。
6. patch writerは一つに限定し、重複起動でも同一work-itemを二重確定しない。
7. artifact同期は一問ごとに実行せず、queue終了時又は変更をまとめられる境界でgroup単位に一度実行する。手動再生成は残す。

## 主な変更候補

- `tools/question_review_console/qualification_runs.py`
- `tools/question_review_console/qualification_workflow.py`
- `tools/question_review_console/work_versions.py`
- `tools/question_review_console/server.py`
- `tools/question_review_console/static/app.js`
- `document/operations/local_question_review_console.md`
- `tests/test_question_review_qualification_runs.py`
- `tests/test_question_review_qualification_flow_recovery.py`
- `tests/test_question_review_qualification_workflow.py`
- `tests/test_question_review_server.py`

## 回帰検証候補

- 58問のうち1問を失敗させ、57問が一度だけ検証・確定される統合テスト
- 再起動後に57問を再処理せず、失敗問だけを再実行するテスト
- 失敗問の後続工程だけが止まり、次の問題へ進むテスト
- fingerprint変更時だけ確定済み問題を再queueするテスト
- artifact同期がqueue全体で一度だけ呼ばれ、失敗してもpatch確定を失わないテスト
- `.venv/bin/python -m unittest tests.test_question_review_qualification_runs tests.test_question_review_qualification_flow_recovery tests.test_question_review_qualification_workflow tests.test_question_review_server`
- `.venv/bin/python -m unittest discover -s tests -p 'test_question_review_*.py'`
- `.venv/bin/python scripts/check/check_00_source_immutability.py`
- `node --check tools/question_review_console/static/app.js`
- `git diff --check`
