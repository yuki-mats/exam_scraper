# 柔道整復師の一問処理・受け渡し性能改善

## 対象と原因

- 改善前run: `20260907T201309463857-00f3bd37`。7,045問、46,737工程、100並列。
- 2026-09-08 07:22頃の直近1〜4時間: 約333〜349検証済み工程/時。
- 直近2時間のattempt中央値: model実行約6.6秒、model executor待ち約200.5秒、patch tool待ち約194.4秒。各指標は別の境界を測るため合算しない。
- 不変planは145.69 MB。最大fieldはstagePlans約78.9 MB、questionExecutions約34.2 MB。`compact_parent_snapshot()`が一問を準備・生成・確定へ渡すたびに、questionExecutions以外の全体計画をdeepcopyしていた。
- 保存済み実データを使った読取専用の測定で、そのdeepcopyは1回1.52秒（warm時1.53秒）。100問分の受け渡しだけで約150秒に相当する。

## 改善と維持する条件

実装commit: `34619b046`（mainへcommit、origin/mainへpush済み）。

`RunSnapshot`が不変planを参照し、消費側が読むfieldだけをその消費側専用にdeepcopyする。親の可変manifestと対象一問の工程stateは都度取得する。全件summaryも検証済みplanを読取専用で参照し、各stateのselfHash計算前の不要deepcopyを取り除いた。

model、推論high、prompt、品質基準、工程順、許可field、source identity、原文保護、hash比較、patchとwork versionのtransaction、rollback、保存済み候補の再利用条件は変更していない。原文・既存ID・本番Firestoreは変更しない。

## 検証

- `tests.test_question_review_run_snapshot`、`tests.test_question_review_question_run_state`、`tests.test_question_review_qualification_runs`: 202件合格。
- question work queue、write transaction、explanation quality、question patch proposal: 71件合格。
- 大きな未読fieldを複製しないこと、参照した可変値を兄弟問題と共有しないこと、summaryを高速化してもstate改変をselfHash検証で拒否することを追加テストした。
- 保存済みplanとcompact manifestを使った100回の代表的な受け渡しは合計0.07秒。これは受け渡し部分だけの測定で、処理全体の高速化倍率ではない。
- warm状態の全7,045問のstate読込・検証は5.42秒。hash検証は全件実行した。
- sourceファイルのGit差分なし。再開時にも対象316ファイルの原文不変検証に合格した。

## 再開

旧serverへ通常終了を要求し、未確定transactionがないことを確認。旧processの終了後、改善版serverを1個だけ起動した。復旧後の旧runは検証済み416問・3,418工程を保持し、retrySafe=true。

2026-09-08 07:45にGUIの「未完了の問題を再開」から、残り6,629問・43,319工程を100並列で開始した。

- 新run: `20260908T074529124867-8615f9c4`
- 再開元: `20260907T201309463857-00f3bd37`
- 保存先: `output/question_review_console/workflow_runs/judoseifukushi/20260908T074529124867-8615f9c4/`
- 30分監視を再設定。設定値だけでなく検証済み工程/時、待ち時間、保留、成果物の品質を追う。
- 1日以内には初期整備だけでも約1,805工程/時が必要。独立評価・保留解消の時間は別途必要であり、初回準備と定常処理を分けて測定する。

## 再開後の初回実測

07:50の102 attemptではmodel executor待ちの中央値0.026秒、最大0.248秒。patch tool待ちは観測41件の中央値7.528秒、最大7.541秒。実model同時稼働ピーク98問を観測した。初回工程plan生成・再開直後の古い保留対象を含む短い観測であり、全期間又は定常状態の代表値とはしない。

07:55時点で8問・60工程が検証済み、残り6,621問、保留41問。親集計だけでなく各question stateも照合した。attemptのreceiptValidatedだけでは内容の保留を含むため、完了数は工程stateのvalidatedとnot_applicableで判定する。

07:54の文章品質再検査では、explanation工程stateがvalidatedで、そのvalidation attemptのcandidateを持つ10問・34解説を対象にした。対応するprojected inputへ候補のfield更新を適用し、explanation_style_issues（correctChoiceText・choiceTextListを渡す）、law_evidence_utilization_issues、law_audit_quality_warningsを再実行して違反0件。法令問題0問・計算問題0問であり、この標本から医学的内容の正確性又は全体の品質を保証しない。独立評価は未実施。

受け渡しの待ち時間は短縮を確認したが、1,805検証済み工程/時以上の定常throughputはまだ未確認。したがって1日以内の完了は現時点で確約できない。30分監視で初回準備と定常処理を分け、残数と期限に必要な速度を更新する。全件の最終機械検証と独立評価が完了するまで、品質完了とは扱わない。
