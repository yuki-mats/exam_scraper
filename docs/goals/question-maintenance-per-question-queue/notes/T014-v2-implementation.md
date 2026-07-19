# T014 問題整備V2 実装receipt

## 結果

問題整備を「modelが共有patchを編集する仕組み」から「modelは複数問の構造化候補を返し、serverが一問ずつ検査・確定する仕組み」へ置き換えた。開始範囲は資格・年度・回・工程のまま維持し、batch数と同時model turn数は入力token量と失敗状態から自動調整する。

## 安全境界

- source identityと反映先をmodel実行前に一意解決する。解決できない問だけを保留する。
- patch、工程版、progress、result、receiptはserverだけが保存する。
- 一問のpatchと工程版を同じtransactionで確定し、失敗時はその問だけを戻す。
- 複数問turnでも一問ごとに永続checkpointを保存する。再起動後は確定済み問を回収し、checkpointのない問だけを再実行する。
- 内容不備は該当問、provider又はschema不備は該当batchだけをqueue末尾へ戻す。通常問題を先に進め、同じ問の品質試行は3回で止める。
- `artifactSync`はqueue終了後に確定年度ごと一度だけ実行する。patch確定と分離し、手動再生成も残す。
- 公式過去問へ05独自問題化工程を自動混入させない。

## 実UI証跡

- 親run `20260719T150723625245-effe24f2`で、2019年の法令監査40問を9 turn（3〜5問/turn）へ自動分割した。
- 初回で37問を確定し、失敗した3問だけを3問batchとしてqueue末尾で2回再試行した。成功済み37問は再実行しなかった。
- 3問を理由付き保留後、独立する55問の問題集工程を11 turn（4〜6問/turn）で開始した。前工程の失敗を58問全体へ波及させなかった。
- 検証用server再起動で、旧回収処理が複数問childを一問writerと誤認する不備を発見した。一問checkpoint、未確定transactionだけのrollback、確定済み問の回収を実装し、再起動回帰テストで固定した。
- 修正後の最新版UIでガス主任技術者甲種2019年の58問previewを開き、`自動・最大性能`、`入力別の自動batch・最大32turn`、`検査と確定は1問ずつ`、開始可能状態を確認した。新しいrunは開始していない。

## 検証

- `test_question_review*.py`: 503 tests OK
- `py_compile`: OK
- `node --check`: OK
- `check_00_source_immutability.py`: 5,799 files OK
- `git diff --check`: OK

実UIで生成された既存output patch差分は破棄・混在commitせず、実装差分から分離した。
