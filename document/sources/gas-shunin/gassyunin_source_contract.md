# gassyunin.com抽出契約

この文書は、`scrape_gassyunin.py`が`gassyunin.com`から取得する本文・選択肢のsite固有契約です。共通scrape規則は[スクレイピングworkflow](../../operations/scraping_workflow.md)を参照してください。

## 正本領域

選択肢系fieldは、詳細内の`各選択肢の判定`セクションをsourceとして取得します。

- `choiceTextList`
- `choiceTextMarkedList`
- `correctChoiceText`
- `explanation_choice_snippets`
- `judgeChoiceMarkers`

`questionBodyText`は`<h2>問N</h2>`の直後から、最初の`(1)`、`(イ)`又は`イ`形式の選択肢記号までを取得します。

## parser契約

`各選択肢の判定`見出しから次の`h3`までにある`div.statement-judge-correct|wrong`を順番に読みます。

| HTML | field |
| --- | --- |
| `blockquote` | `choiceTextList` |
| `blockquote`内の誤り強調 | `[wrong]...[/wrong]`を含む`choiceTextMarkedList` |
| `judge-header` | `correctChoiceText` |
| `correct-text-line`, `judge-meta` | `explanation_choice_snippets` |

`正解: (n)`から`answer_result_text`と`answer_result_inferred_correct_choice_numbers`を作り、番号を独自にリマップしません。

## 監査field

- `questionChoiceMarkers`: 問題本文側の記号列。
- `judgeChoiceMarkers`: 判定セクション側の記号列。
- `choiceMarkerSource`: 通常は`judge`。
- `markerAlignmentMode`: `judge_matches_question_markers`、`judge_priority_mismatch`、`judge_only`、`question_only`。
- `markerMismatchDetected`: 両記号列の不一致。
- `answerResultNumbersRemapped`: `false`。

判定セクションが欠ける問題を、問題本文から推測して自動補完しません。source conflict又はreview対象として扱います。

## 再監査条件

- 新年度で公式PDFと判定セクションの順序が一致しない。
- 判定セクションに本文の省略・要約が増える。
- `markerMismatchDetected=true`が増加する。
- 判定セクション自体が欠ける問題が増加する。

このいずれかが起きた場合は、公式PDFとのspot checkを行い、本契約とparser testを同時に更新します。
