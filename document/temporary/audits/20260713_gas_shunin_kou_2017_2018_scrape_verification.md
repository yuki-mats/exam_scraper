# 甲種ガス主任技術者2017・2018 scrape監査

## 対象

- `https://gassyunin.com/exam/kou/kou_2018/`
- `https://gassyunin.com/exam/kou/kou_2017/`
- `output/gas-shunin-kou/questions_json/<year>/00_source/question_<year>_*.json`

## 検証方法

2026-07-13に各ページを再取得し、ライブHTMLを現行parserで読み直して保存済みJSONと照合した。`各選択肢の判定`がある問題は本文、科目、問番号、URL、選択肢、正誤、正答番号を比較した。数値選択肢問題は`.num-choice-box`又は`ol.choice-list`から選択肢を再抽出し、`正解: (n)`と保存値を比較した。

件数はJIAの[試験の問題と解答](https://www.jia-page.or.jp/exam/examination/answer/)が示す法令16問、基礎15問、ガス技術27問とも照合した。

## 結果

| 年 | HTTP | HTML / 保存件数 | 科目内訳 | 判定形式 | 数値選択肢形式 | 内容不一致 |
| --- | --- | --- | --- | --- | --- | --- |
| 2018 | 200 | 58 / 58 | 法令16、基礎15、製造9、供給9、消費9 | 49 | 9 | 0 |
| 2017 | 200 | 58 / 58 | 法令16、基礎15、製造9、供給9、消費9 | 48 | 10 | 0 |

- 116問すべてに一意な`source_question_id`があり、保存側の重複はない。
- 116問すべてで問題本文、科目、問番号、source URL、正答番号がライブHTMLと一致した。
- 数値選択肢19問は、選択肢本文、正答位置、`正解` / `不正解`配列まで一致した。
- `markerMismatchDetected=true`、空の`choiceTextList`、重複`sourceQuestionKey`、重複`sourceUniqueKey`はいずれも0件だった。
- 画像参照は両年度とも0件で、未保存画像はなかった。

## source側の既知表記

2017年ページの見出しは`2017年（平成２７年）甲種`となっており、和暦だけが西暦と一致しない。保存済み`examLabel`はsource表記を保持し、`examYear`と`examOccurrenceId`は西暦の`2017`で正しく保存されている。scrape時にsource本文を訂正しない契約のため、これは抽出ミスではない。

## 判定範囲

この監査で確認したのは`gassyunin.com`に表示された内容と保存済みsourceの一致である。2017・2018の全問題を当時の公式問題PDF・公式正答と独立照合した監査ではないため、site側の解説内容そのものの正確性は別工程の対象とする。
