# Udemy Business取得契約

この文書は、ログイン済みUdemy Business講座の演習テストを取得するときのsite固有契約です。共通のID、`00_source`、画像、再取得ルールは[スクレイピングworkflow](../../operations/scraping_workflow.md)を正本とします。

## 取得単位

- presetの`source_list_group_id`にはcourse slug、`output_list_group_id`には取得元と資格版を識別できる安定名を保存する。
- 講座内の演習テストを列挙し、quiz ID、講座表示件数、各演習テストの問題数を照合する。
- 正答と解説を表示できるレビュー画面から、問題文、選択肢、正答、全体解説、分野、画像、参照URLを一括取得する。
- レビュー画面を作るために新しい試行が必要な場合は、未回答のまま完了してレビューを開く。Cookie、認証情報、試行IDは保存しない。
- 講座タイトル、講座概要、演習テスト開始画面、レビュー画面の件数が一致しない場合は、実際に列挙できたレビュー画面の一意な問題番号集合を取得対象とする。タイトル記載の旧件数に合わせて切り捨てず、差異を日付付き監査へ記録する。

## 認証とbrowser export

- Cookie、ID、password、tokenをrepo、browser export、`00_source`、reportへ保存しない。
- ログイン済みブラウザで同一originのレビュー画面を開き、`scripts/scrape/udemy_browser_export_helpers.mjs`で構造化した一時JSONだけをexportする。
- Python scraperは`UDEMY_BROWSER_EXPORT_PATH`又は`--browser-export`で一時JSONを読み、ブラウザ操作とsource保存を分離する。
- browser exportは一時入力であり、作業完了後に恒久artifactとしてrepoへ保存しない。

## 再取得の標準手順

1. ログイン済みブラウザで各演習テストのレビュー画面を開き、`extractUdemyReviewPage`でquiz単位に取得する。
2. `createUdemyBrowserExport`で作成したexportへ`upsertUdemyQuiz`で全quizを集約し、`writeUdemyBrowserExport`で一時JSONを書き出す。
3. 一時JSONの全件数とquiz別件数を確認してから、次のrunnerを実行する。

```bash
UDEMY_BROWSER_EXPORT_PATH=/tmp/udemy_browser_export.json \
  python3 scripts/scrape/run_qualification_scrape.py \
  <preset名> <output_list_group_id>
```

runnerは既存IDも毎回照合し、新規、更新、同一、消失をreportへ記録する。全問題の生成と画像取得が成功するまで`00_source`を更新しない。

## IDと保存

- 取得元の安定キーはcourse slug、quiz ID、演習テスト内の authored question numberの組合せとする。試行ID、review URL、本文hashはIDに使わない。
- 1問を`question_udemy-<quiz_id>-<question_number>.json`へ保存する。同じキーは同じファイル名と`source_question_id`を維持する。
- authored question numberは演習テストの固定問題番号として扱う。再取得で同じquiz ID内の問題番号集合が変わった場合は、追加、更新、消失としてreportへ記録し、消失問題は自動削除しない。
- question URLはcourse slug、quiz ID、問題番号を含む安定locatorとし、試行固有のresult URLは保存しない。
- 画像は公開可能なsource URLからローカルへ保存し、source URLと公開用Storage URLの件数を一致させる。

## 完了条件

1. 講座表示の演習テスト数と列挙したquiz ID数が一致する。
2. 各演習テストの表示問題数、browser export数、保存数が一致する。
3. 全講座の表示問題数と、quiz ID・問題番号の一意な組合せ数が一致する。
4. 全問に問題文、2件以上の選択肢、1件以上の正答、全体解説がある。
5. 問題ID、`source_question_id`、question URLが重複しない。
6. 参照画像がすべて保存され、空ファイルがなく、source URLとStorage URLの件数が一致する。
7. 再実行で同じ問題集合を再照合し、新規、更新、同一、消失を区別したreportを残す。

資格ごとのquiz ID、件数、分野内訳、日付付き取得結果はgenerated report又は`document/temporary/audits/`へ置き、この契約へ固定値として複製しません。
