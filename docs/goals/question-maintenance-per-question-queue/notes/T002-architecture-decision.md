# T002 一問完結型queueの設計判断

## 判断

最初の縦断sliceは、top maintenanceを資格・年度・回の親queueへ変更し、一問ごとの判断・修正案作成を隔離workspaceで並列化する。複数workerが共有patchを直接編集すると更新を失うため、本体patchへの適用・検証・工程版記録は単一commit writerが一問ずつ行う。

## 状態契約

- 親run: scope、順序、集計、最終`artifactSync`だけを持つ。
- `questionExecutions[]`: exact identity、`queued/preparing/prepared/committing/validated/blocked`、現在工程、試行回数、停止理由、child run IDs、入力・出力fingerprintを持つ。
- prepare child: 常に一問・一工程。run固有workspaceだけを書き、修正案と検証根拠をreceiptへ保存する。複数childを上限付きで並列実行できる。
- commit writer: prepared itemを一つずつexact record scopeへ適用する。`receiptValidated=true`になった時点で、その問題・工程だけを確定する。
- work-item key: `sourceQuestionKey/reviewQuestionId/sourceRecordRef + stageId`。表示IDや配列位置を所有権に使わない。
- fingerprint: 問題の`stateHash`、工程`policyFingerprint`、工程IDを含む。再開時にvalidated itemの出力hashが現状と一致すればskipし、変化したitemだけqueueへ戻す。
- 親の完了状態は各問題から導出する。`blocked`を含んでもqueue処理自体は完了できるが、UIでは「一部保留」と理由を明示し、全件成功と表示しない。

## 実行・retry契約

1. 親scopeから問題順を固定する。
2. 複数問題のprepare childを並列実行する。各childは本体patchを変更せず、run固有の修正案だけを生成する。
3. 単一commit writerがprepared itemを一問ずつ適用・検証・確定する。確定後、その問題の次工程をprepare queueへ入れる。
4. child又はcommit失敗時は、その一問scopeだけを破棄又はrollbackする。問題を`blocked`へ置き、他のprepared itemと次の問題を進める。
5. transport中断などrollback完了を確認できる一時失敗だけ一回自動retryする。failed receipt、strict validation、identity、必須field、patch競合は自動retryせず理由付きで止める。
6. restart又は再開ではvalidated itemを再実行しない。preparing itemは安全に再queueし、prepared itemはfingerprint確認後にcommitを再開する。
7. Merge・Convert・upload-readyの公開用同期は全queue走査後にgroupごと一回だけ実行する。`artifactSync`失敗はpatch確定を取り消さず、手動再生成を残す。

## 最初のWorker slice

backendの状態、実行、永続化、公開run payload、簡潔なUI集計、運用正本、回帰テストを一つのsliceで実装する。実データのガス主任技術者甲種2019年実行は、このsliceの自動テストと00_source検査が通った後のJudge taskで行う。
