# gassyunin.com抽出契約

この文書は、`scrape_gassyunin.py`が`gassyunin.com`から取得する本文・選択肢のsite固有契約です。共通scrape規則は[スクレイピングworkflow](../../operations/scraping_workflow.md)を参照してください。

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

このいずれかが起きた場合は、公式PDFとのspot checkを行い、本契約とparser testを同時に更新します。
