# T003 実装receipt

## 結果

開始範囲は資格・年度（回）・工程のまま維持し、内部処理を一問・一工程の永続queueへ分離した。各問の判断案は最大2件のread-only threadで準備し、本体patchへの適用・検証・作業版記録は単一writerが直列に確定する。

失敗は、安全なrollbackを確認できた問題と依存後続だけを`blocked`にする。年度別mergeの失敗も当該年度だけを保留し、再開時は後続writerの前に再mergeする。確定直後又はmerge中にserverが停止しても、manifest、identity、receiptを照合して確定済みitemを再実行しない。照合不能な差分だけをfail-closeにする。

patch確定後のMerge・Convert・upload-ready・upload dry-runは、確定した年度ごとに集約する。`artifactSync`失敗でpatchを戻さず、手動再生成も残した。本番Firestoreと`00_source`は変更していない。

## 検証

- `test_question_review_*.py`: 400件成功
- Python compile、JavaScript構文、`git diff --check`: 成功
- `00_source`: 4,525ファイル、差分なし
- 実行前の既存output差分39ファイル: ハッシュ一致
- 独立安全監査: APPROVE
- GoalBuddy監査: `approve_next`

次はT004で、ガス主任技術者甲種2019年を実UIから開始し、並列準備、単一writer、問題別停止、成果物readbackを確認する。
