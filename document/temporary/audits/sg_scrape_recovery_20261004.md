# SG取得不備の修復・検証結果（2026-10-04）

判定: **今回判明した取得不備は解消し、取得工程の全件検査に合格した。** 全12回・704解答単位・484ページを取得し、午後解説201問分と116問の解説図表の参照を復旧した。午後の残り43問は取得元自体が解説未掲載であり、取得失敗に数えていない。学習内容の整備・独立評価・アプリ公開は次工程である。

## 修復内容と検証範囲

- 午後の解答欄から次の設問までのDOMを読み、正答・解説を対応付ける。ラッパーdivの有無に依存せず、次の設問の正答・解説を借用しない。
- 午後の設問共通解説は共通prefixへ保存する。上付き・下付き等の数式、番号付きリスト、CSS生成の「ア・イ」等を保持する。カナ記号は取得元stylesheetの定義でも照合した。
- 問題・選択肢・解説の用途ごとに画像を取得し、解説画像の出典URL・保存参照を保持する。本文画像209ファイルに解説画像123ファイルが加わり、参照画像は332ファイルになった。
- 独立checkerは解説文字列・選択肢別の解説記号・用途別の画像名と順序・出典URL・実画像を照合する。`--verify-live-images` では全参照画像を再読込してbytesのSHA256も照合する。途中失敗時はlive検証成功flagを立てない。
- 標準runnerはSGの既存回も毎回取得する。source IDに基づいて既存のfile名・位置・公開ID・原問IDを維持し、新しいsource IDだけを末尾chunkへ追加する。既存IDの消失・ID変更・不完全な取得では保存せず停止する。既存sourceを部分取得で上書きしない。

実装・取得仕様は `document/sources/README.md` の既存SG項目へ反映した。今回の主な変更先は `scrape_sgsiken.py`、`scripts/check/check_sgsiken_acquisition.py`、`scripts/scrape/run_qualification_scrape.py` と対応テストである。

## 全件の結果

| group | 解答単位 | 取得ページ | 参照画像ファイル | 不一致 |
| --- | ---: | ---: | ---: | ---: |
| 202601 | 15 | 15 | 12 | 0 |
| 202501 | 15 | 15 | 13 | 0 |
| 202401 | 15 | 15 | 6 | 0 |
| 202301 | 15 | 15 | 7 | 0 |
| 201902 | 83 | 53 | 33 | 0 |
| 201901 | 81 | 53 | 59 | 0 |
| 201802 | 81 | 53 | 33 | 0 |
| 201801 | 78 | 53 | 34 | 0 |
| 201702 | 81 | 53 | 50 | 0 |
| 201701 | 77 | 53 | 28 | 0 |
| 201602 | 81 | 53 | 28 | 0 |
| 201601 | 82 | 53 | 29 | 0 |
| 合計 | 704 | 484 | 332 | 0 |

午後244問中、取得元に解説がある201問の解説を保存し、取得元と照合した。解説画像があるのは午前86問・午後30解答単位、計116問。解説画像の出典URLは123件。332ファイルの全画像について331個の異なる出典URLから再読込し、全ファイルのSHA256一致を確認した。同じ出典画像を用途別の別ファイルへ保存したものがあるため、URL数とfile数は異なる。

再取得前の704問すべてについて、source ID・公開ID・原問ID・file名とfile内位置・正答番号が一致した。source36ファイルのSHA256はmanifest36件と一致し、更新hashは標準runnerが登録した。2019年秋期の過去の欠損sourceに関する歴史的IDの制約は初回receiptのとおりで、今回証明したID維持は再取得開始時の704問を基準とする。

## 実行記録

実装commit: `9fb98eaac`（解説・画像・保存・独立検査）、`38d389292`（CSS肢記号・検証flag）。全年度取得は前者で実施。復旧後の目視でCSS肢記号の欠落を追加発見し、後者で影響する旧形式7回を再取得した。前者の検査通過だけでは完了にせず、後者で全12回の最終検査を実施した。

関連テスト100件は98件成功・live専用2件skip。解説削除、番号・数式・CSS記号の欠落、別問題画像への参照、正常画像だがbytesが異なる場合、別設問の正答借用、source追加・消失・ID変更、部分上書きの拒否を検証した。構文チェック・diff checkも成功。全リポジトリのテストは実施していない。

全12回のsource quality-gate、sourceの選択肢/正答整合性検査、source必須項目検査が成功。最終mergedは未作成で、merged検査の対象は0件だった。正答patchも作成しておらず、patch coverageのCLIにはsource/patchの明示指定が必要なため適用対象なし。空のpatchを作って検証成功を装っていない。sourceの仮形式・正答ラベルは01/02aで正式に確定する。

```text
.venv/bin/python scripts/scrape/run_qualification_scrape.py sg --group-retries 0
.venv/bin/python scripts/scrape/run_qualification_scrape.py sg 201901 201802 201801 201702 201701 201602 201601 --group-retries 0
.venv/bin/python scripts/check/check_sgsiken_acquisition.py sg --verify-live-images
.venv/bin/python tools/question_bank/question_bank.py quality-gate --qualification sg --mode source
.venv/bin/python scripts/check/check_choice_text_alignment.py --base-dir output/sg/questions_json --stage source
.venv/bin/python scripts/check/check_required_fields.py --base-dir output/sg/questions_json --stage source
```

保存先は `/Users/yuki/development/exam_scraper_work/output/sg/`。最終checker reportは `reports/siken_acquisition_audit.json`（status=passed、liveImageHashesVerified=true）。各回の取得・更新IDは `scrape_reports/<group>.json`、HTMLは `verification/dojo/`、sourceは `questions_json/<group>/00_source/`、画像は `question_images/<group>/`。

ローカルの詳細証拠:

- `tmp/sg_recovery_verification_receipt_20261004.json`: 対象commit、実行時dirty状態、Python依存版、code/preset入力hash、最終source/HTML/reportのhash、CSS定義の証拠、日時、成功状態。
- `tmp/sg_identity_baseline_20261004.json`、`tmp/sg_recovery_identity_and_counts_20261004.json`: 再取得前後のID・保存位置・正答番号の照合。
- `tmp/sg_full_recovery_20261004.log`、`tmp/sg_label_recovery_20261004.log`、`tmp/sg_live_image_audit_20261004.log`: 実取得と全件画像照合。
- `tmp/sg_repair_tests_20261004.log`、`tmp/sg_source_quality_gate_20261004.log`、`tmp/sg_source_alignment_20261004.log`、`tmp/sg_source_required_20261004.log`: 対象テストとsource検査。
- `tmp/sg_repair_negative_control_20261004.log`: 強化した検査が修復前データを不合格にした結果。

今回の検査は取得元との抽出一致を確認するもの。取得元自体の解説の現行性・正答の妥当性や、学習用日本語の独立評価を代替しない。sourceを手作業では変更していない。別作業のNW/SC/整備システム変更を維持し、今回のcommitはSG関連ファイルだけを対象とした。定期実行は設定していない。
