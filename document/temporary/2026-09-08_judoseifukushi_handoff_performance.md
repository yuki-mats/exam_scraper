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

## 09:02の監視と追加改善

09:02:43時点のquestion state実測では、389問・1,746工程が検証済み、残り6,240問、保留127問。直近30分は804工程、毎時1,608工程で、改善前の約4.7倍になった。ただし残り41,573工程の初回整備だけでも約25.9時間の見込みで、1日期限には不足する。新runの観測時間は77.1分であり、2時間・4時間の実測があるとは扱わない。

model executor待ちp50/p90は9.09/22.13秒、patch tool待ち17.59/39.25秒、patch lock待ち0.09/23.52秒、model終了からattempt終了74.39/124.73秒。attempt全体の中央値163秒に対してmodel実行中央値15.94秒であり、受け渡し・確定側に改善余地が残る。認証エラーは0件。

保存済み実planを使って、対象を一問に絞る前に全年度のphase planを複製する処理と、全6,629問のprogressTargetsを複製してから一問を探す処理を特定した。warm時の全phase複製は中央値0.107539秒、全progressTargets複製・検索は0.049854秒、一問だけの複製は0.000010秒だった。これらは部分測定であり処理全体の倍率ではない。

追加実装commit: `bb0c0fce3`（mainへcommit、origin/mainへpush済み）。

- `_dynamic_question_phase_plan`はscopeを一問に置換してからdeepcopyし、消費側の独立性を保つ。
- 親planの不変target一覧を一度だけ索引化し、question snapshotには該当一問だけを独立複製して渡す。
- 共有工程には従来どおり全体snapshotを渡す。model、推論、prompt、検査、確定条件は変更しない。
- 上記7テストmodule計275件合格。置換される兄弟scopeの不要複製がないこと、nested policyの独立性、一問snapshotで兄弟targetを読まないことを回帰検証した。

確定済みexplanation316問・958解説を再構成し、既存の文章・法令利用検査違反0、空欄0、40文字以上の非自明な完全重複0を確認。法令12問、計算0問で、独立評価は未実施。機械検査の通過は医学的正確性又は全体品質の保証ではない。保留127問の内訳は集約回答review125問と法令sidecar不整合2問。保留候補にもcandidate validationの成功receiptが付くため、その成功数を完成問題数と混同しない。

## 追加改善版のGUI再開

旧serverを通常終了し、旧processの終了を確認して改善版serverを一つだけ起動した。復旧後の旧runは398問・1,915工程の検証済み状態を保持し、`retrySafe=true`、未確定transactionは0件だった。

09:16にGUIの「未完了の問題を再開」から、残り6,231問・41,404工程を再開した。

- 新run: `20260908T091630008834-4418b79b`
- 再開元: `20260908T074529124867-8615f9c4`
- 保存先: `output/question_review_console/workflow_runs/judoseifukushi/20260908T091630008834-4418b79b/`
- GUIで100並列の選択を確認し、manifestの`llmProfile.limits`の`questionParallelism`、`llmCallConcurrency`、`effectiveQuestionConcurrency`がすべて100であることを読み戻した。
- 再起動中だけ一時停止した30分監視を、新runを対象としてACTIVEへ戻した。期限は最初の依頼から1日であり、再開に合わせて延長しない。

09:19にmodel稼働97問・ピーク98問を観測した。09:20の初回attempt集計ではmodel executor待ちp50 0.43秒、patch tool待ち1.08秒、model終了からattempt終了15.39秒となった。ただし短い初期標本であり、定常速度の改善率又は完了時間の根拠にはしない。09:21:57にheartbeat更新とquestion stateの検証済み工程0→3の増加を確認した。親manifestは周期集計のため、この瞬間は0工程表示で遅れていた。新runの認証エラーは0件。独立評価・保留解消を含む1日以内の完了可否は、追加改善後の継続実測で判断する。
