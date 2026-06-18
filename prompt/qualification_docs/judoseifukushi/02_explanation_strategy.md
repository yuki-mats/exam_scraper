# 柔道整復師 `explanationText` 作成方針

この文書は、`03_prompt_add_explanationText.md` の補助前提である。

## 優先順位

1. `00_source/question_*_*.json`
2. `20_merged_1/question_*_merged.json`
3. `00_source` に不足がある場合の一次情報

## 一次情報の使い方

- `explanation_common_prefix`、`explanation_common_summary`、`explanation_choice_snippets` がある場合は、まずそれを土台にする。
- `00_source` の `explanation_common_prefix`、`explanation_common_summary`、`explanation_choice_snippets` が欠損・全空・根拠不足なら、外部 Web の一次情報を使ってよい。
- 外部 Web を使う場合は、法令・制度は e-Gov、行政資料は厚生労働省や関係省庁、学校保健などは文部科学省、資格制度は試験実施団体の公開資料を優先する。

## 書き方

- 各選択肢について、正しいか誤りかを明示する。
- 誤りの選択肢は、どの語句・条件・数値が誤りかまで書く。
- 1問1答の短文に寄せず、受験者が復習しやすい説明にする。
- 選択肢ごとの説明は、正答の決め手が重複しても、同じ結論を選択肢ごとに書く。

## 柔道整復師で特に注意する点

- 関係法規、免許、業務範囲、広告、罰則は、条文や制度の整理が必要になる。法規・制度問題は `04_law_reference_policy.md` を先に確認する。
- 公衆衛生・医療統計は、定義語の置き換えが起こりやすいので、調査名や指標名を正確に確認する。
- 医療法規や制度は、出題当時と現行法が異なる場合があるため、過去問としての正答と現在の理解を分けて説明する。

## 今回の運用

- `lawReferences` を別途作る前提ではなく、必要な根拠は `explanationText` に簡潔に書く。
- ただし、法令名・制度名・統計名は省略しすぎず、受験者が再確認できる粒度は残す。
- 法規・制度・届出・免許・業務範囲が絡む問題は、迷ったら `lawGroundedExplanationNotNeeded` を `false` に倒す。
