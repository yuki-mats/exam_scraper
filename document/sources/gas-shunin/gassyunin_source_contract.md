# gassyunin.com抽出契約

この文書は、`scrape_gassyunin.py`が`gassyunin.com`から取得する本文・選択肢のsite固有契約です。共通scrape規則は[スクレイピングworkflow](../../operations/scraping_workflow.md)を参照してください。

> [!IMPORTANT]
> `gassyunin.com`は既存`00_source`の取得経路を説明するためだけに残す。問題整備、矛盾監査、正答修正、解説作成、Firestore修正では同サイトを閲覧せず、問題文・選択肢・正答の根拠にも使わない。これらは[JIA公式PDFアーカイブ](official_exam_pdf_archive.md)にある検証済みの問題PDFと正答PDFを必ず照合して確定する。

## 根拠としての境界

- このsiteから取得した本文、選択肢、`judge`欄、`正解: (n)`は、取得時点のsnapshotであり、公式性又は正確性の証明ではない。
- 公式過去問の問題文・選択肢を確定する前に、同年度・同種別の公式問題PDFを目視し、PDFページ、冊子ページ、科目、問番号を記録する。
- 公式正答を確定する前に、同年度・同種別の公式正答PDFを目視し、科目、問番号、正答番号を記録する。
- 問題PDF又は正答PDFのいずれかを検証できない場合は、site表示から補完せず`hold`にする。
- 解説では技術資料や法令を補助根拠に使えるが、元の問題文・選択肢・公式正答を決める根拠の代わりにはしない。

## 正本領域

選択肢系fieldは、問題形式に応じて詳細内の`各選択肢の判定`又は問題直下の明示的な数値選択肢をsourceとして取得します。

- `choiceTextList`
- `choiceTextMarkedList`
- `correctChoiceText`
- `explanation_choice_snippets`
- `judgeChoiceMarkers`

`questionBodyText`は`<h2>問N</h2>`の直後から、最初の`(1)`、`(イ)`又は`イ`形式の選択肢記号までを取得します。

## parser契約

### 各選択肢の判定

`各選択肢の判定`見出しから次の`h3`までにある`div.statement-judge-correct|wrong`を順番に読みます。

| HTML | field |
| --- | --- |
| `blockquote` | `choiceTextList` |
| `blockquote`内の誤り強調 | `[wrong]...[/wrong]`を含む`choiceTextMarkedList` |
| `judge-header` | `correctChoiceText` |
| `correct-text-line`, `judge-meta` | `explanation_choice_snippets` |

`正解: (n)`から`answer_result_text`と`answer_result_inferred_correct_choice_numbers`を作り、番号を独自にリマップしません。

組合せ問題の`正解: (n)`は、取得元に表示された元の組合せ肢番号を指します。`choiceMarkerSource`、`markerAlignmentMode`、`judgeChoiceMarkers`、`correctChoiceText`などは、取得内容を失わずに保存し、公式PDFとの差を検出するための監査fieldです。これらの件数が一致しても最終正誤とはみなしません。公式問題PDF上の記述と組合せ肢、公式正答PDF上の正答番号を照合し、その対応から各記述の正誤を確定します。対応を一意に確定できない場合は、推測で補正せず停止します。

### 数値選択肢

計算問題などで`各選択肢の判定`がなく、問題直下に`.num-choice-box`又は`ol.choice-list`がある場合は、`strong`の連番と表示テキストを直接読みます。`正解: (n)`が単一かつ選択肢範囲内であることを必須とし、次のように保存します。

- `questionType`: `group_choice`
- `choiceTextList`, `choiceTextMarkedList`: HTMLに明示された選択肢
- `correctChoiceText`: 正答だけ`正解`、その他は`不正解`
- `explanation_choice_correctness`: `correctChoiceText`と同じ配列

選択肢番号が非連続、正答番号が複数、又は範囲外の場合は停止します。

## 監査field

- `questionChoiceMarkers`: 問題本文側の記号列。
- `judgeChoiceMarkers`: 判定セクション側の記号列。
- `choiceMarkerSource`: 通常は`judge`。
- `markerAlignmentMode`: `judge_matches_question_markers`、`judge_priority_mismatch`、`judge_only`、`question_only`。
- `markerMismatchDetected`: 両記号列の不一致。
- `answerResultNumbersRemapped`: `false`。

判定セクションと明示的な数値選択肢の両方が欠ける問題を、問題本文から推測して自動補完しません。source conflict又はreview対象として扱います。

## source表記の保持

`examYear`と`examOccurrenceId`は見出し中の西暦を使います。和暦表記に不整合があってもscrape時に訂正せず、`examLabel`にはsourceの見出しを保持します。訂正が必要な場合はpatch又はsource conflictで扱います。

## 新規取得時の実行順

次の順序は、新規`00_source`を作成した同一作業内かつmanifest登録前だけに使います。登録済みsourceには`--fix`やrepair scriptを実行しません。

```bash
python3 scripts/scrape/run_qualification_scrape.py gas-shunin-kou <year>
python3 scripts/check/check_gas_shunin_00_source_contract.py \
  --qualifications gas-shunin-kou --list-group-ids <year> --fix
python3 scripts/check/check_gas_shunin_00_source_contract.py \
  --qualifications gas-shunin-kou --list-group-ids <year>
python3 scripts/check/check_00_source_immutability.py --record-new
```

`scrape_gassyunin.py`が数値選択肢を直接取得するため、`scripts/pipeline/repair_gas_shunin_num_choice_sources.py`は過去データ移行用であり、通常の新規scrapeでは使いません。

## 再監査条件

- 新年度で公式PDFと判定セクションの順序が一致しない。
- 判定セクションに本文の省略・要約が増える。
- `markerMismatchDetected=true`が増加する。
- 判定セクション自体が欠ける問題が増加する。

このいずれかが起きた場合は、公式PDFとの全対象照合を行い、本契約とparser testを同時に更新します。通常の問題整備でも、spot checkだけで確定せず、対象の各問について問題PDFと正答PDFを確認します。

公式PDFは[JIA公式PDFアーカイブ](official_exam_pdf_archive.md)のcatalogから選び、ローカル保存済みの問題・論述問題・正答を使います。保存済みPDFがある年度について、JIA公式一覧又はInternet Archiveを問題ごとに検索し直しません。
