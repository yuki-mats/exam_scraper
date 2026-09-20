# kako-mon.com 抽出契約

この文書は、`kako-mon.com`に共通するHTML抽出条件だけを定義します。保存先、ID、画像、`00_source`保護は[スクレイピングと`00_source`](../../operations/scraping_workflow.md)を正本とします。

## 入口と資格差分

- 共通実装は`scrape_kakomon.py`から起動する`scripts/scrape/kakomon.py`とする。
- 資格ごとの差は`config/scrape_presets.json`へ置き、`scraper_type`は`kakomon`、`list_first_page_url_template`は対象回の問1、`expected_question_count`はその回の全問題数を指定する。
- 問題URLは`/<資格slug>/<試験回>-<10問単位の区分>-<3桁の問番号>/`とする。資格slugを実装へ固定しない。

## 問題ページの抽出

1. `.question-container`内の`.question-body`と`.question-subs`の文章（A〜Dの記述、表の文字を含む）を問題文、`data-cnum`付き`.choice-body`を選択肢として取得する。別枠が画像だけの場合は画像参照を保持し、本文に余分な空行を加えない。
2. 選択肢番号は1から5までの連番であることを確認する。
3. 正答は`.question-footer[data-corr]`、`.choice-correct`、`.answer-body`の「答」の3表示を独立に取得し、完全一致しないページを保存しない。
4. ページ見出しの問番号、URLの問番号、URL区分を照合する。一級ボイラーの旧形式はA・問1〜20を通し番号1〜20、B・問1〜20を21〜40として対応させ、元URLは変更しない。
5. 問題・選択肢画像は取得元URLとStorage参照を対で保持する。全問の取得と検証が完了するまで、再取得結果を既存`00_source`へ反映しない。
6. 上付き・下付き文字は既存scraperと共通のUnicode変換で保存する。このサイトの`small`は添字として扱い、`.fraction`は分子・分母を明示する`(分子)/(分母)`へ変換する。通常の文字列連結によって指数や分数の意味を失わせない。

## 完了条件

- presetの`expected_question_count`と取得件数が一致する。
- 問番号が1から連続し、canonical keyと`source_question_id`が全件一意である。
- 各問に5肢と1個の正答があり、文字のない肢には画像がある。
- 再取得では既存と今回の`source_question_id`集合が一致する。差分がある場合は自動削除又は部分上書きをしない。
- 初回導入時は実ページを目視し、画像問題は取得画像も照合する。公式問題・正答が公開されている資格では、正答列を公式資料と全件照合する。

## 全掲載範囲の取得確認

一級ボイラーでは資格トップページから試験回と掲載リンクを抽出し、presetの試験回一覧と照合する。各回は40問であり、古いA/B表記も1回として数える。

取得HTMLは`output/<資格>/verification/html/<試験回>/<問番号>.html.gz`に保存する。全件取得後、`python scripts/check/check_kakomon_acquisition.py boiler1 --index-url https://kako-mon.com/bo-1/`で、serializerとは別経路で問題領域全体、肢順、正答、年度・期、問番号、canonical URL、次問リンク、画像の対応と保存実体を検査する。文字の照合ではHTML表示に影響しない空白だけを無視する。

検査は`00_source`を変更せず、`output/<資格>/verification/acquisition_audit.json`へ件数、source hash、失敗箇所を記録する。機械検査全件と実画面の抜取目視は区別して報告し、サイトとの一致を現行法令上の正しさと混同しない。
