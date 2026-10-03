# 情報処理安全確保支援士.comの取得・照合

指定された[公開一覧](https://www.sc-siken.com/sckakomon.php)を入口に、前身の情報セキュリティスペシャリストを含む公開回を確認する。URL、資格名、対象回の対応、期待件数、午後の取得範囲は`config/scrape_presets.json`の`sc`だけで定義する。共通手順は[スクレイピングworkflow](../../operations/scraping_workflow.md)、保存契約は[artifact契約](../../operations/artifact_contract.md)を参照する。

## 取得する内容

- 午前Ⅰ・午前Ⅱ：既存の`sgsiken` parserで問題文、選択肢、取得元正答、取得元解説、分類、画像を`00_source`へ保存する。試験区分はURLと`examLabel`で区別し、同じ問番号の別区分を混同しない。
- 午後の記述式：午前の選択式JSONへ変換しない。公式問題・解答PDF、取得元の問題別解説PDF、公開されている記述式HTMLと画像を資料として保存する。年度によるPDF・HTMLの差は公開リンクを直接列挙する。
- 平成23年特別試験は前期groupへ対応付ける。元号はリンクの表示から確認し、URL中の数字だけで元号を推測しない。

通常の取得は次のとおり。PDF照合には`pypdf`と`pdfplumber`、画像検査には`Pillow`を利用する。

```bash
.venv/bin/python scripts/scrape/run_qualification_scrape.py sc
.venv/bin/python scripts/check/check_scsiken_acquisition.py \
  --download-pdfs --download-afternoon-html
.venv/bin/python tools/question_bank/question_bank.py quality-gate \
  --qualification sc --mode source
```

再取得するときは標準runnerへ`--force`を付ける。ID、chunkの位置、ファイル名は維持し、全回の公開一覧と正答PDFも再確認する。取得範囲の制御値`include_afternoon_questions`はboolで検証し、runnerが`SCRAPER_INCLUDE_AFTERNOON_QUESTIONS`として既存parserへ渡す。

## 取得元の意味を保つ抽出

不可視のHTMLコメント・script・styleを本文へ混入させない。本文・全選択肢・解説では、上線、指数、添字、分数、根号、番号付きリストを保持する。`li1`等のクラスは括弧付き番号、`maru1`等は丸数字をCSSで表示するため、単なる装飾として捨てない。クラス末尾の数字をそのまま番号とせず、それぞれのCSS counterの増加・リセットを反映する。リストの開始番号・個別番号・番号形式を維持する。Unicodeに正確な文字がない指数・添字は`^(N+1)`、`_(A)`のように元の文字と範囲を保持し、大文字を小文字へ変えたりラテン字をギリシャ字へ置き換えたりしない。

取得元解説では、選択肢解説の前にある用語説明・通常のリスト・直接text node、選択肢解説後の結論も保存する。取得元の解説を抽出することと、03工程で公開用の解説を作ることは別の責務である。

## 独立検証

`check_scsiken_acquisition.py`はsourceを変更しない。解析に使ったHTMLと保存済みsourceを問題URL単位で対応付け、次を確認する。

1. live公開回とpresetの全回一致。新規回・削除・対応変更を検出したら取得元を確認し、presetを更新してから実行する。
2. 各回の午前Ⅰ・午前Ⅱの問番号集合、取得元URL集合と保存済みURL集合の完全一致。
3. 試験年度・区分・問番号、本文と全選択肢と解説の取得元テキストの保持、IDの一意性。解説のCSS番号付きリストは、生成番号と対応する文章を独立変換して一緒に照合する。
4. 同じ問題の取得元正答、保存正答、IPA公式解答PDFの正答の一致。古いPDFの数字間空白と日本語CMapを扱うが、欠落・重複問番号を許容しない。画像だけのPDFは、一問ずつ目視転記した`.answers.json`をPDF hashと照合し、PDFが変われば再確認する。
5. 問題・選択肢画像の件数とローカル存在、午後HTML集合、公式・解説PDFと午後資料のSHA256一致。

公式解答と取得元が矛盾した場合は検査で停止し、その問のHTMLと公式問題・解答PDFを一問ずつ目視する。取得元の順序変更・改題等を確認できた場合は、source内容hashと公式PDF hashに結び付けた照合receiptを`verification/source_variants.json`へ保存する。照合済み差異を含む取得結果は`passed_with_publication_holds`とし、公式正答一致件数と公開保留を分けて記録する。内容が変わればreceiptを再確認する。どちらかに決め打ちしてsourceを修正せず、公開形の確定は後工程で行う。取得漏れや表記損失の修復はparserを修正し、標準scraperで全対象を再取得する。

## 照合の証拠

`output/sc/`をローカル正本とし、次へ保存する。

| 資料 | 保存先 |
| --- | --- |
| 午前の取得JSON | `questions_json/<group>/00_source/` |
| 午前の画像 | `question_images/<group>/` |
| 解析に使ったHTML | `verification/dojo/<group>/` |
| 公式PDF | `official_pdfs/<group>/` |
| 問題別の午後解説PDF | `afternoon_explanations/<group>/` |
| 午後HTML・画像 | `afternoon_html/<group>/` |
| PDF取得URL・hash・ページ数 | `verification/official/<group>/documents.json` |
| 全問の照合結果 | `reports/acquisition_verification.json` |

取得元の問題・解説・画像・PDFを新たにGitの追跡対象へ追加しない。Gitには設定、抽出・照合実装、回帰test、保全hashと件数だけの確認記録を保存する。取得済みであることは、問題ごとの整備・独立評価又はアプリ公開が完了したことを意味しない。
