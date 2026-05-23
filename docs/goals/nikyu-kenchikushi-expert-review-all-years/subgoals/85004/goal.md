# 二級建築士 2018年度(list_group_id=85004) 専門家レビュー

## Objective

`output/2nd-class-kenchikushi/questions_json/85004` の全設問について、二級建築士の専門家・問題作成者・参考書著者の観点で 1問ずつ精査し、`correctChoiceText` を 99.99% 水準で確認する。あわせて `21_explanationText_added/` の `explanationText` が教材として公開できる品質であることを確認し、必要なら patch を更新する。

## Non-Negotiable Constraints

- 会話・報告は常に日本語で行う。
- 変更時には作業内容と保存先を明示する。
- patch 本文をスクリプトで自動生成しない（件数確認・差分確認・check は可）。
- 元ファイル（`00_source/` / `20_merged_1/`）は書き換えない。
- `correctChoiceText` は `questionIntent`、`answer_result_text`、`choiceTextList`、元解説を突き合わせて確認する。
- `explanationText` は `prompt/03_prompt_add_explanationText.md` に従う。

## Run Command

```text
/goal Follow docs/goals/nikyu-kenchikushi-expert-review-all-years/subgoals/85004/goal.md.
```
