# SG保留73問の独立内容評価（2026-10-05）

## 対象と結果

- 対象は `tmp/sg_held_recovery_20261004/final_receipt.json` の `content_confirmed` 73問。元の保留74問のうち、ユーザーが公開対象外と決定した `0c92d2a32566c62927cd2b73` は含めない。
- 現行評価方針5.8のrunは `output/sg/reports/content_evaluations/20261005_sg73_policy58/`。73問すべてに別sessionの結果を保存した。合格30問、要再整備41問、判定不能2問。`completedCount=71`、runの終端は `needs_followup`。合格30問もGUIの正式評価合格や公開承認ではない。
- 判定不能は `54265d327ee65384d3062b75`（2018年春・午後問2設問2(2) h）と `6c48881e85c5770a03739933`（同 i）。原文の設問本文は空欄g～iをまとめて問い、各recordの `questionLabel` がh/iを区別する。一方、単問として表示する `questionBodyText` には対象空欄の指定がない。全肢の正誤を推測で埋めず、対象空欄が単問表示にも明確になる整備を要する。
- 要再整備41問の個別指摘は同runの `questions/<questionId>.json` と `triage.json` に保存した。`triage.json` の指摘は独立評価結果であり、原文と照合した修正承認済みデータではない。工程別の指摘項目数は03解説39、05問題文・条件9、01形式6、02a正答6、03b監査3で、一問に複数項目がある。

## 証拠と境界

- 計画した36バッチは73個の一意な問題IDに一致し、除外IDは含まない。73問すべてで実行sessionのthread・session・turn・modelを確認した。判定完了71問は全選択肢の判定がそろい、残り2問は理由付き `inconclusive` とした。画像を宣言した63問に添付bytesの証跡があり、欠落はない。
- 評価終了時の `protectedInputsUnchanged=true` と `policyUnchanged=true` を現在のfile hashから再確認した。`00_source` 36ファイル・704問・704一意source IDは保留復旧時のhashと一致する。patchと原文は評価で変更していない。
- 先行run `output/sg/reports/content_evaluations/20261004_sg73_full/` は、実行中に評価方針が5.7から5.8へ更新され `policyUnchanged=false` となったため、現行評価の結果へ流用しない。現行5.8のrunだけを上記集計に使う。
- 両runとも `existingCreditsApproved=false`、`publicationEvaluationPromoted=false`、`firestoreWritten=false`。追加creditsとFirestore書込みは行っていない。

## 正式評価へ進めない条件の確認

- `202301` の対象1原問だけをsource binding三要素で限定mergeし、5公開documentをconvertしてupload dry-runまで成功した。`--skip-update-category-counts` を指定し、部分生成物から資格全体のcategory件数を書き換えていない。
- しかし標準 `quality-gate --qualification sg --list-group-id 202301` は、同groupのsource 15問に対し現行patchが10問分で、対象外5問のpatch coverage不足により失敗した。限定生成物の成功を年度全体の品質gate合格として扱わない。
- 同1問 `28f25fad94354f225a31c535` のGUI正式評価previewは `evaluableCount=0`、`machineReady=false`。停止理由は `identity_mismatch` と `source_answer_difference_unapproved`。正式評価runは開始していない。
- 他groupでも未処理204問と既知の `artifactSync=blocked` が残る。73問の内容評価結果から正式評価projection、公開準備、Firestore反映への昇格は行っていない。

今後は41問の指摘を各原文・全選択肢と照合して再整備し、h/iの表示対象を明確にする。全groupのpatch coverageと公開用データの品質gateを通した後、現在内容に対するGUIの別session正式評価を実施する。
