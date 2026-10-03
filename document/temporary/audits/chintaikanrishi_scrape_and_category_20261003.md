# 賃貸不動産経営管理士：分類準備・取得確認（2026年10月3日）

## 分類

ユーザー提供の目次画像6枚から、5 folders・70 questionSetsを作成した。小見出し94件を漏れなく対応付け、近接単元の境界を記録した。分類JSONのschema-only dry-runと問題整備システムのcategory ready判定を通過した。問題ごとの分類付与は未実施。

正本は`output/chintaikanrishi/category/category.json`、根拠と境界は`prompt/qualification_docs/chintaikanrishi/03_category_preparation.md`。

## 取得件数

| 年度 | 過去問道場 | 過去問.com | 過去問.comに掲載されていない問番号 |
| --- | ---: | ---: | --- |
| 2015 | 40 | 39 | 7 |
| 2016 | 40 | 40 | — |
| 2017 | 40 | 40 | — |
| 2018 | 40 | 40 | — |
| 2019 | 40 | 39 | 8 |
| 2020 | 50 | 50 | — |
| 2021 | 50 | 50 | — |
| 2022 | 50 | 50 | — |
| 2023 | 50 | 49 | 49 |
| 2024 | 50 | 50 | — |
| 2025 | 50 | 49 | 9 |
| 合計 | 500 | 496 | 4問 |

過去問道場の年度一覧の全リンクと期待件数を照合し、2015〜2025年の500問を取得した。既存の過去問.comは、11年度の全一覧URL集合と取得済みの問題URL集合を照合して496問を取得した。上の4問を別の取得元の内容で過去問.comのsourceへ埋めず、取得元ごとのスナップショットを保持する。

両取得元の計996 source recordsは、canonical keyで500問へ対応する。996問を独立した試験問題数として数えない。

## 保存・保全・検証

- 過去問道場：`output/chintaikanrishi/questions_json/2015〜2025/00_source/`
- 過去問.com：`output/chintaikanrishi/questions_json/88001〜88011/00_source/`
- 取得前に存在した7ファイル・143問のsource ID、公開ID、原問ID等を保持した。
- 保全manifestに記録があり、取得前にはローカルに存在しなかった15ファイルを標準scraperの全件取得で復元した。既存ファイルの名前・record位置は維持した。
- 全44 sourceファイルのSHA256を保全manifestと照合した。
- 過去問道場は500件の本文・全候補・正答・全体解説、年度・問番号・source ID・canonical keyの一意性を検証した。正答テキストは同一問題の候補内に存在する。
- source全体のquality gateを通過した。画像参照65件のローカルファイル存在も確認した。
- 関連93 testsが成功し、従来サイトのlive tests 2件は通常どおりskipした。

取得HTML、年度別scrape report、初回更新report、ID保全記録と全体確認receiptは`output/chintaikanrishi/`内へ保存した。全体receiptは`reports/scrape_latest_20261003.json`。取得データと画像はローカル正本として保持し、Gitには実装、設定、分類正本、保全hashとこの確認記録を保存する。

## 番号と既存IDの注意

過去問.comの2024・2025年度は、ページtitleに分野内の番号、h1に試験全体の番号を表示する。parserは同じページのh1に明示された全体番号を採用するよう修正し、両年度を全件再取得した。今回新規取得したrecordのIDは正しいcanonical keyで再生成し、作業開始時から存在した143問のIDは保持した。

既存の`88011`には、11種類の公開IDに複数のsource recordsが対応し、公開IDの重複が38件残る。source IDとcanonical keyは49問すべて一意で、取得内容は最新化済みである。既存公開IDを自動変更せず、重複を`identityConflicts`と全体receiptの`legacyIdentityHolds`へ記録した。このgroupの公開・ID移行は保留する。過去問道場の500問と今回新規取得した2024年分には公開IDの重複はない。

Firestore・Storageへの反映、問題ごとの独立評価、現行法監査は実施していない。
