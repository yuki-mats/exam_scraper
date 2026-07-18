# Ping-t取得契約

この文書は、`mondai.ping-t.com`の「最強WEB問題集」を取得するときのsite固有契約です。共通のID、`00_source`、画像、再取得ルールは[スクレイピングworkflow](../../operations/scraping_workflow.md)を正本とします。

## 取得単位

- 資格ごとの`question_subjects/<subject_id>`を取得単位とする。
- presetの`source_list_group_id`にsubject ID、`output_list_group_id`に取得元と試験版を識別できる安定名を保存する。
- 一覧は`/question_subjects/<subject_id>/questions`から取得し、表示件数、全ページ、全問題IDを照合する。
- 詳細は`/question_subjects/<subject_id>/questions/<question_id>`を使う。演習sessionを作るURLや学習履歴を変更する操作は使わない。

## 認証

- ログイン必須である。認証情報は`~/.config/exam_scraper/secure.env`に置き、repo、`00_source`、reportへ保存しない。
- 通常実行では`PINGT_COOKIES_JSON`又は`PINGT_COOKIE_HEADER`を使う。
- ログイン済みブラウザから取得する場合は、Cookieを読み出さず、同一originのGET結果を一時exportし、`PINGT_BROWSER_EXPORT_PATH`から同じPython parserへ入力する。
- 一覧条件はsession単位で共有されるため、ブラウザ経由のページ遷移を複数タブで同時実行しない。1問ごとに問題ID、本文、カテゴリを照合して一時exportへ保存してから次へ進む。
- ログイン画面へ転送された場合は問題0件として完了せず、認証エラーで停止する。

## 保存と再開

- 1問を`question_ping-t-<subject_id>_<question_id>.json`へ保存する。
- `source_question_id`は資格コード、`ping-t`、subject ID、問題IDを含む。表示順や本文hashはIDに使わない。
- 新規ファイルは排他的に作成し、既存ファイルを上書きしない。途中停止後は取得済み問題IDをskipし、未取得IDから再開する。
- 一覧から消えた既存問題、同じIDの内容競合、一覧と詳細の問題文又はカテゴリ不一致は自動修正せず停止する。
- 一覧文末の`...`は省略表示として、詳細本文が省略前の文字列から始まる場合だけ一致とみなす。一覧にない`(Nつ選択)`が詳細本文の末尾に付く場合も、その定型suffixだけを許容する。保存する問題文は詳細側の全文とする。

## 抽出項目

- 問題ID、カテゴリ、問題文、選択肢、単一・複数選択の別
- 正答番号と、設問意図に基づく選択肢ごとの`correctChoiceText`
- 解説本文、問題・選択肢・解説の画像、参考URL
- 問題固有URL、subject ID、site provenance

「学習テキスト」は問題解説とは別の教材であり、問題ごとの`00_source`へ複製しない。必要な解説ナレッジは取得した問題解説から一般化し、資格別方針へ反映する。

## 完了条件

1. 一覧の表示件数と全ページから収集した問題ID数が一致する。
2. 一覧ID集合と保存ID集合が一致する。
3. 問題ID、`source_question_id`、問題URLの重複がない。
4. 全問に問題文、2件以上の選択肢、1件以上の正答、解説がある。文字のない画像選択肢は画像が必要である。
5. 参照した画像がすべてローカルに保存され、空ファイルがない。
6. 再実行で新規保存が0件となり、既存`00_source`のhashが変化しない。

資格ごとの取得件数やカテゴリ内訳は日付付き監査又はgenerated reportへ置き、この契約へ固定値として複製しない。
