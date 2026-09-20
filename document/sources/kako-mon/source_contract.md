# kako-mon.com 抽出契約

この文書は、`kako-mon.com`に共通するHTML抽出条件だけを定義します。保存先、ID、画像、`00_source`保護は[スクレイピングと`00_source`](../../operations/scraping_workflow.md)を正本とします。

## 入口と資格差分

- 共通実装は`scrape_kakomon.py`から起動する`scripts/scrape/kakomon.py`とする。
- 資格ごとの差は`config/scrape_presets.json`へ置き、`scraper_type`は`kakomon`、`list_first_page_url_template`は対象回の問1、`expected_question_count`はその回の全問題数を指定する。
- 問題URLは`/<資格slug>/<試験回>-<10問単位の区分>-<3桁の問番号>/`とする。資格slugを実装へ固定しない。

## 問題ページの抽出

1. `.question-container`内の`.question-body`を問題文、`data-cnum`付き`.choice-body`を選択肢として取得する。
2. 選択肢番号は1から5までの連番であることを確認する。
3. 正答は`.question-footer[data-corr]`、`.choice-correct`、`.answer-body`の「答」の3表示を独立に取得し、完全一致しないページを保存しない。
4. ページ見出しの問番号、URLの問番号、URL区分を照合する。
5. 問題・選択肢画像は取得元URLとStorage参照を対で保持する。全問の取得と検証が完了するまで、再取得結果を既存`00_source`へ反映しない。

## 完了条件

- presetの`expected_question_count`と取得件数が一致する。
- 問番号が1から連続し、canonical keyと`source_question_id`が全件一意である。
- 各問に5肢と1個の正答があり、文字のない肢には画像がある。
- 再取得では既存と今回の`source_question_id`集合が一致する。差分がある場合は自動削除又は部分上書きをしない。
- 初回導入時は実ページを目視し、画像問題は取得画像も照合する。公式問題・正答が公開されている資格では、正答列を公式資料と全件照合する。
