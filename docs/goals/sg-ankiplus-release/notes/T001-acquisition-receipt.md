# T001 取得・精査結果（2026-10-03）

> 最新状態（2026-10-04）: 再監査で見つかった不備を修復し、全12回・704問・484ページと参照画像332ファイルの全件照合に合格した。午後解説201問分と解説図表を復旧し、全704問のID・保存位置・正答番号も維持した。[復旧・最終検証記録](../../../../document/temporary/audits/sg_scrape_recovery_20261004.md)を現在の取得完了receiptとする。以下は初回・再監査の履歴である。

> 再監査による訂正: 取得工程は未完了。以下の初回記録にある「取得元の解説全文を照合」「取得完了」は撤回する。午後244問の解説がすべて空で、取得元に解説がある201問分が欠落していた。解説画像123 URLの関連付けもない。既存checkerは午後解説・解説画像を検査していなかった。詳細は[再監査記録](../../../../document/temporary/audits/sg_scrape_reaudit_20261003.md)を参照。以下は初回の検査結果を保存した履歴であり、完全取得の証明には使わない。

取得元: https://www.sg-siken.com/sgkakomon.php

年度別過去問12回、704問、484ページ、参照画像209ファイルを取得した。sourceは36ファイル。年度一覧とpreset、各回の一覧URL集合と保存URL集合、本文、選択肢とその順序、正答番号、取得元の解説全文、画像参照数と実ファイルの読み込み、公開IDの重複を照合し、不一致は0件だった。source用quality-gateも全12回で合格した。

| group | 取得問数 |
| --- | ---: |
| 202601 | 15 |
| 202501 | 15 |
| 202401 | 15 |
| 202301 | 15 |
| 201902 | 83 |
| 201901 | 81 |
| 201802 | 81 |
| 201801 | 78 |
| 201702 | 81 |
| 201701 | 77 |
| 201602 | 81 |
| 201601 | 82 |

午後は解答単位で数える。同じ解答群から複数を選ぶ欄は一問、異なる名前付き空欄は別問。予想問題とサンプル問題はこの年度別過去問の取得範囲に含めていない。

保存先は `/Users/yuki/development/exam_scraper_work/output/sg/`。`questions_json/<group>/00_source/` が取得データ、`question_images/<group>/` が画像、`verification/dojo/` が取得時HTML、`scrape_reports/` が取得・更新IDの記録。全件照合reportは `reports/siken_acquisition_audit.json`、年度ごとのreportは `reports/siken_acquisition_audit_<group>.json`。生成データと取得HTMLはGitの既存ignore方針に従ってローカルへ保存し、Gitにはツール・設定・source hash・このreceiptを保存する。

2019年秋期の既存source4ファイルは開始時から欠損していた。残存mergedの一問にあるIDは継承・照合済み。失われた他の歴史的recordとの全ID一致を証明する資料はない。既存2025年度sourceの15問のIDは、標準保存処理の新旧照合で維持した。復旧は検証用取得を経て標準scraperで全件取得し、成功後に対象scopeのhashをmanifestへ登録した。手作業でsourceを修正していない。

取得元HTMLとの独立照合で、午後の共通本文・図表欠落、複数選択の重複ID、classのない設問見出しの見落とし、解説の直接text・入れ子list・末尾結論の欠落、番号付きlistの参照記号喪失を検出・修正した。上付き・下付きではUnicodeにない文字や大文字の意味を保ち、代替表現で保存する。

標準runnerはlive年度一覧を照合し、新年度未設定・掲載消失を検出すると停止する。SGは全件の保存前にHTML照合を行い、保存後に独立checkerで画像を含めて再照合する。今後も同じ入口で再取得・検証できる。今回は定期実行を設定していない。

検証コマンドと詳細ログ:

```text
.venv/bin/python scripts/check/check_sgsiken_acquisition.py sg
.venv/bin/python tools/question_bank/question_bank.py quality-gate --qualification sg --mode source
.venv/bin/python scripts/scrape/run_qualification_scrape.py sg 202601 --force --group-retries 0
```

ログは `tmp/sg_audit_final_20261003.log`、`tmp/sg_source_quality_gate_20261003.log`、`tmp/sg_runner_integration_20261003.log`。関連テストはSG parser・独立checker・preset・標準runnerの再試行・共有parserを使う賃貸管理とSCを確認した。全リポジトリのテストは実行していない。

標準runnerの2026年度再取得では、年度一覧確認・保存前HTML照合・保存後の独立checkerが成功し、新規0・更新0だった。SG36ファイルのSHA256はmanifest36件と一致している。取得・検証ツールのcommitは `059df2b72`。

共通hash台帳のcommit `b730e6198` には、同時作業で登録されていたSCのhashも含まれる。最初の全hash照合はSCの再取得中ファイルで失敗したが、shellの後続commitを止められず実行した。SCの内容品質をこのreceiptで検証済みとは扱わない。その後、更新された3件を実ファイルと照合して `300c93a5d` に記録し、SG36件とSC99件について現在の実ファイルと台帳がそれぞれ完全一致することをread-onlyで確認した。失敗を成功扱いにせず、今回の取得完了判定はSGだけを対象にする。

この完了は取得工程に限る。取得元との一致は、解説内容の現行性や学習用の品質評価を代替しない。SG sourceの形式・正答ラベルは既存取得契約の値であり、01/02aで一問ずつ正式に確定する。整備patchと最終mergedはまだない。merged検査では対象fileなしだったため、公開品質の合格を意味しない。以後、整備・法令対象判定・独立評価・データ公開・アプリ公開を順に検証する。
