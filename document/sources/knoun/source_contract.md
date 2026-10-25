# Knounの画像取得

`scripts/scrape/knoun_images.py`は、Playwrightでドリルを巡回する独立CLIです。Computer Useはサイト構造の確認に使い、取得処理はこのツールで実行します。既定はヘッドレスであり、ブラウザを前面へ出しません。

## 対象と入口

既定対象は、ユーザーが指定した2026年版eco検定公式問題集の模擬問題1〜5（各85問）です。取得元URL、書籍識別子、期待件数の値はCLI実装の定数を正本とします。これは実施済み試験の年度別過去問と同じ区分にはしません。

```bash
.venv/bin/python -m pip install -r requirements_scrape_browser.txt

# 最初は2問で、画像と解説の対応・保存内容を確認する
.venv/bin/python -m scripts.scrape.knoun_images \
  --groups 1 --max-questions 2 --allow-learning-history-updates --login-stdin

# 5組を順番に取得する。同じコマンドで中断後も再実行できる
.venv/bin/python -m scripts.scrape.knoun_images \
  --allow-learning-history-updates --login-stdin

# ブラウザを開かず、保存済み全画像の存在とhashを検証する
.venv/bin/python -m scripts.scrape.knoun_images --verify-only
```

Chromeは通常利用中のprofileへ接続せず、`~/.config/exam_scraper/knoun-browser/`を使います。専用profileはアクセス権700で作成し、cookie・password・tokenをexport又はrepoへ保存しません。本人が画面でログインする場合だけ`--headed`を明示します。この場合、ログインを待つ間は`LOGIN_REQUIRED`を表示します。ログイン期限切れ、画像取得失敗、識別子不一致では確定済み成果物を保持して停止します。

本人が認証情報を渡した実行では`--login-stdin`を使えます。ID・passwordはechoを切った端末入力で受け取り、Knounのログイン画面へ入力した後に変数を破棄します。コマンド引数、環境変数又は保存fileには載せません。

巡回時は「はじめから」「わからない」「次へ」を使うため、学習履歴を更新します。ユーザーが許可した実行に限り`--allow-learning-history-updates`を指定します。今回のeco検定取得では2026-10-25に許可されています。

## 取得・保存と再開

- 問題集の見出しとその直下の開始ボタンを対応付け、画面の85問表示を照合する。
- 各問題で実際に設定された問題frame・解説frameのURLを読む。未観測の連番URLを生成して探索しない。
- 書籍ID・問題集ID・問題IDを両URLから独立に取り出し、一致した対だけ保存する。問題固有の安定URLを`sourceQuestionId`にする。
- 同じ専用browser contextのHTTP requestでframe HTMLと原PNGを取得する。ログインページへのredirect、他domain、別問題画像、破損画像を保存しない。
- 原画像は`output/eco-kentei/question_images/knoun-2026-mock-<組>/raw/`へ置き、同階層の`capture_manifest.json`へ対応URL、相対path、SHA256、サイズを一問ずつ確定する。
- 再開時は保存済み画像の全hashを検査する。途中の組は先頭から再照合し、同一bytesの画像を保持する。差異は自動上書きせず停止する。
- 同じprofileで二つの取得処理を動かさない。画像の全件数・一意なURL集合・問題と解説の対応がそろった組だけ`images_complete`にする。

「わからない」は選択式・記述式の内部IDへ固定せず、表示中の操作名で選ぶ。回答操作の前にwindow loadを待つ。画像やframeの取得成功だけでは、Knounの回答用の時刻が初期化されたとは判断しない。

対象検証は`.venv/bin/python -m unittest discover -s tests -p 'test_knoun_images*.py'`。読込みが遅いframeと記述式のボタンを持つローカルfixtureで遷移を確認する場合は、`RUN_KNOUN_BROWSER_TEST=1`を付けて同じコマンドを実行する。fixtureはヘッドレスで動き、Knounへ通信しない。

`images_complete`は画像取得だけの完了です。OCR・目視照合・問題整備・公開の完了を意味しません。

## 文字起こしと後工程

画像取得後は、原画像を保ったままOCR草案を別に保存し、問題文、全選択肢、正答、解説を一問ずつ元画像と照合します。数字、単位、否定、空欄、図表のラベル、正答記号を推測で補いません。判読不能又は問題・解説間の矛盾は未確認として止めます。

文字起こしを検証した後に、標準scraperで新規`00_source`へ登録します。現段階の画像取得CLIは`00_source`、patch、Firestoreを変更しません。

**eco検定の全問は必ず05独自問題化を経由します。** 以後は[独自問題作成ワークフロー](../../operations/original_question_authoring_workflow.md)と[問題整備の入口](../../operations/exam_pipeline_manual_and_automation.md)に従います。文字だけのスキャン画像を、問題成立に必要な図表と取り違えないよう、文字起こし段階で区別します。
