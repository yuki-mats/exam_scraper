# 柔道整復師 01-04 prompt 実行ワークフロー

## バッチ単位

- 基本単位は `00_source/question_<year>_<n>.json` の 1 ファイル単位で進める。
- 1993-2004 は 1 ファイル 25 問、2005-2026 も原則 1 ファイル 23-25 問なので、queue 更新と検証を file 単位で閉じる。
- 01・02・04 は同一 source file を連続で処理し、03 はその file の正誤と category が固まったあとに着手する。

## 1ファイルあたりの順序

1. `00_source/question_<year>_<n>.json` を読み、`questionType` を目視確認する。
2. `10_questionType_fixed/question_<year>_<n>_questionType_fixed.json` を固定名で上書きする。
3. `scripts/merge/00_merge_all.py <year> --base-dir output/judoseifukushi/questions_json` を実行し、`20_merged_1/` を更新する。
4. `20_merged_1/question_<year>_<n>_merged.json` を読み、`questionIntent` を確認する。
5. `15_correctChoiceText_fixed/question_<year>_<n>_correctChoiceText_fixed.json` を固定名で上書きする。
6. 再度 merge し、`correctChoiceText` の整合を確認する。必要時のみ `23_correctChoiceText_fixed/` を使う。
7. `21_explanationText_added/question_<year>_<n>_explanationText_added.json` を固定名で上書きする。
8. `22_questionSetId_linked/question_<year>_<n>_questionSetId_linked.json` を固定名で上書きする。
9. `30_merged_2/` の最新 merged を確認し、queue の該当設問を更新する。

## 柔道整復師で先に見えた注意点

- `questionType` の初期値は全件 `true_false` だが、単純知識想起の 1問1答型は `flash_card` へ振り替える余地がある。
- `questionIntent` は「誤っている組合せ」「誤っている組み合わせ」のような表現を negative phrase に含めないと取りこぼす。
- `questionSetId` は `category.json` の `judoseifukushi_qs02_*` のような anatomy 系 ID が序盤に多い。問題文だけでなく選択肢論点まで見て割り当てる。
- `03_explanationText` は 01・02・04 の後で着手しないと、正誤と category の揺れをそのまま解説に持ち込む。

## 毎ファイルの確認コマンド

```bash
python3 scripts/check/check_questiontype_patch_coverage.py \
  --source output/judoseifukushi/questions_json/<year>/00_source/question_<year>_<n>.json \
  --patch output/judoseifukushi/questions_json/<year>/10_questionType_fixed/question_<year>_<n>_questionType_fixed.json
```

```bash
python3 scripts/check/check_question_intent_correct_choice_text_distribution.py \
  --output-root output/judoseifukushi/questions_json/<year>/30_merged_2 \
  --glob 'question_<year>_<n>_merged*.json'
```

```bash
python3 scripts/check/check_explanation_patch_coverage.py \
  --source output/judoseifukushi/questions_json/<year>/20_merged_1/question_<year>_<n>_merged.json \
  --patch output/judoseifukushi/questions_json/<year>/21_explanationText_added/question_<year>_<n>_explanationText_added.json
```

```bash
python3 scripts/check/check_question_set_patch_coverage.py \
  --source output/judoseifukushi/questions_json/<year>/20_merged_1/question_<year>_<n>_merged.json \
  --patch output/judoseifukushi/questions_json/<year>/22_questionSetId_linked/question_<year>_<n>_questionSetId_linked.json \
  --category output/judoseifukushi/category/category.json \
  --questionset-only
```

## queue 更新ルール

- `review01QuestionType`: `10_questionType_fixed` の coverage check が通ったら `done`
- `review02QuestionIntent`: `15_correctChoiceText_fixed` と merge 後の `questionIntent` / `correctChoiceText` 整合が確認できたら `done`
- `review02CorrectChoiceText`: `23_correctChoiceText_fixed` が不要なら `auto_ok`、必要なら patch 作成後に `done`
- `review03ExplanationText`: `21_explanationText_added` の coverage check が通ったら `done`
- `review04QuestionSetId`: `22_questionSetId_linked` の coverage と merged compare が通ったら `done`
- `reviewDecision`: 1設問の 01-04 がすべて揃ったときに `complete`

## Git の扱い

- `output/` は ignore 対象なので、成果物を commit する場合は `git add -f` を使う。
- stage 対象は `judoseifukushi` と goal 配下、および今回必要なスクリプト変更だけに限定する。
- 他資格の dirty worktree は戻さず、混ぜない。
