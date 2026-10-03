# 番号式過去問道場の取得契約

`chintaikanrishi-siken.com`は既存の`sgsiken` scraperを共有し、番号式ページのparserでDOMの差を扱う。年度と期待件数は`config/scrape_presets.json`を正本とする。

## 一覧と問題

年度一覧の`NN.html`への実リンクを列挙し、各問題ページを取得する。URLの西暦年度・問番号と`h2`の年度・問番号を照合する。本文は`.mondai`、回答候補は`ol.selectList`、正答表示は`.answerBox .answerChar`、解説は`section.kaisetsu`を読む。

選択肢は同じ問題の4候補を保持する。サイトが`li`の閉じタグを省略している場合は、HTML parserで子要素として扱われた次の候補を取り除き、当該候補だけを抽出する。正答番号は正答表示と選択肢の`data-answer="t"`から独立に読み、両者が一致しなければ保存しない。問題文から正答や候補の真偽を推測しない。

## 個数・組合せ問題と解説

本文中の`ol.kanaList`は設問内の命題であり、回答候補とは別の情報である。ア・イ・ウなどの記号はCSS表示だけで本文から消えないよう、取得テキストに明示する。個数・組合せ問題の解説にある`kanaList`も、ア・イ・ウの命題に対応するため、回答候補1～4の`explanation_choice_snippets`へ流し込まない。取得した全体解説を`explanation_common_prefix`へ保存する。

通常の回答候補に対応する解説リストだけを、同じ問題の候補別スニペットとして保持する。形式判定の`questionType`は取得時には付けず、01工程で問題全体から確定する。正答の選択肢テキストは同一問題の候補から取り出し、正答番号の根拠も保存する。

## 出典・画像・完了条件

source IDは資格と問題固有URLから、canonical keyは資格・年度・問番号から生成する。再取得では既存の公開ID・原問IDを維持する。本文・候補・解説の画像を共通画像処理で取得し、画像取得が不完全な場合は停止する。

各年度のHTMLは`output/chintaikanrishi/verification/dojo/<year>/`へ圧縮保存する。全問の取得、ID重複、本文・候補・正答と期待件数を確認した後に`00_source`を保存し、scrape reportとsource保全manifestを更新する。問題への分類付与、現行法の監査、整備・評価・公開は取得後の各工程で行う。
