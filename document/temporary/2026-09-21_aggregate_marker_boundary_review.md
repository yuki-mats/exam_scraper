# 集約回答の先頭記述欠落の修正

## 原因と変更

同じ裸の英字ラベルでも、空白・改行後は `latin_inline`、句点直後は
`latin` として別系列へ分類していた。先頭を前者で検出すると、重複位置の
排除により後者の系列へ入らず、a〜d のうち b〜d だけが候補になることがあった。

`scripts/common/aggregate_answer_decomposition.py` で、この二つの検出規則を
同じ `latin` 系列へ統合した。正規表現、原文、offset、hash、レビューの
承認条件は変更していない。候補は引き続き原文の連続範囲だけから生成する。

## 再現と検証

- 半角小文字と全角大文字について、改行・句点・空白が混在する4記述の回帰を追加。
  修正前は両方失敗し、修正後は全記述の原文一致を確認した。
- 二級ボイラー `60023/question_60023_2.json#12`
  （取得元 `https://boiler2.kakomonn.com/questions/93068`）と、
  貸金業務取扱主任者 `93008/question_93008_1.json#15`
  （取得元 `https://kashikin.kakomonn.com/questions/69514`）の保留を再現。
  修正後はそれぞれ A〜D、a〜d の4記述を含む候補が一つ生成される。
- 原本全件の候補差分を照合。一級1,360問・ビル管理士1,435問は差分0、
  二級914問中77問、貸金547問中73問で候補が変わる。
- 影響範囲の既存 `target` patch 55件（二級）について、保存済み全spanが
  修正後の候補と完全一致。貸金の当該範囲には既存 `target` patchはなかった。
  原本の書換えや、既存の正しいspanの再作成は行っていない。
- 関連回帰317件成功：`tests.test_aggregate_answer_decomposition`、
  `tests.test_question_review_qualification_runs`、
  `tests.test_question_review_primary_law_evidence`、
  `tests.test_question_review_adaptive_scheduler`、
  `tests.test_question_review_codex_app_server`。

## 稼働中の扱い

4資格の現在runは継続する。全run終端後にserverを修正版へ切り替え、
`aggregate_review_hold` を含む保留・未完了を既存のpreview/startで再整備する。
候補変更は `candidateSetHash` に反映されるため、古い集約レビューcheckpointは
同じ候補として再利用されない。修正・テストの成功は全問整備完了を意味しない。

待ち時間にはMacの `Maintenance Sleep` の反復も含まれていた。
PID10450に限定した1時間のidle-sleep抑止を実施したが、OSのメンテナンス
スリープは継続したため、電源接続と通常復帰をユーザーへ依頼した。
画面消灯と永続的な電源設定は変更していない。
