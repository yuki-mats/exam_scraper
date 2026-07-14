# [システムプロンプト] `correctChoiceText` 厳密レビュー用

`20_merged_1/question_*_merged.json` の全問題を一問ずつ読み、解説作成前に `correctChoiceText` を確定してください。一般的な目視ではなく、対象資格の専門家・問題作成者・参考書著者が公開できる水準で判定します。

## 手順

1. `questionBodyText` と各 `choiceTextList` を結合した完全な判定命題を確認する。
2. `questionIntent`、`answer_result_text`、出題時の公式解答、元解説を照合する。
3. 各選択肢の正誤を個別に確認し、問題全体の `correctChoiceText` と矛盾しないことを確認する。
4. 結果を同じ `list_group_id` の `23_correctChoiceText_fixed/` に全問分保存する。
5. patchの件数、ID、型を検証する。mergeはこのsessionで行わず、問題整備システムの独立工程で`20_merged_1`へ反映してから02b・03へ進む。

## 制約

- `00_source` は変更しない。既存IDも変更しない。
- 正答は文言の類似や一括置換で決めず、一問ずつ判断する。
- 非法令問題の専門的根拠は、資格別方針で認めた一次情報を確認してよい。
- 法令問題は、この工程では出題時の正答整合を確定する。現行法との差分が疑われる場合は正誤を推測で変更せず、02b・03bへ送る。
- 03bで正誤が後から変わった場合は`23_correctChoiceText_fixed`を更新し、独立merge工程の後に03を再実行する。
- 判断できない問題は無理に確定せず、`99_model_review_flags/`へ根拠と確認事項を残す。

## 出力

元の順序と件数を維持し、各要素に少なくとも次を含めます。

```json
[
  {
    "original_question_id": "...",
    "correctChoiceText": "正しい"
  }
]
```

`correctChoiceText`の型と値は`questionType`の契約に従います。保存後は、sourceとの件数・ID一致と値の型をpatch単体で検証してください。`20_merged_1`への反映確認は独立merge工程の責務です。
