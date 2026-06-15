# 柔道整復師 01-04 prompt 下準備

## 現状

- 対象データは `output/judoseifukushi/questions_json/1993-2026/00_source/` に存在する。
- ローカル集計で 34 年、316 source files、7,600 問を確認済み。
- `output/judoseifukushi/category/category.json` は 12 folders、167 questionSets。
- `category.json` は folder ID / questionSet ID の重複なし、questionSet の folder 参照切れなし。
- 現時点の柔道整復師配下は `00_source` のみで、01-04 の patch/merge 出力は未作成。

## 年別件数

- 1993-2004: 各 200 問、各 8 source files。
- 2005-2019: 各 230 問、各 10 source files。
- 2020-2026: 各 250 問、各 10 source files。

## 重要な初期所見

- 全 7,600 問の `questionType` が `true_false` で初期化されている。
- `questionIntent` は `select_correct` が 5,885 問、`select_incorrect` が 1,715 問。
- `explanation_common_prefix` と `explanation_common_summary` は全件空。既存の解説断片は主に `explanation_choice_snippets` 側を確認する。
- 1993 問1 のように、問題文が「正しいのはどれか。」だけの設問があるため、01 と 04 は問題文だけではなく選択肢と既存解説断片まで読む必要がある。

## 準備成果物

- `goal.md`: 作業方針と完了条件。
- `state.yaml`: 現時点の棚卸し状態と次の作業位置。
- `inventory.json`: 年・source file 単位の件数一覧。
- `queue.jsonl`: 7,600 問の question-level queue。

## 次の開始位置

最初の作業対象は次。

```text
output/judoseifukushi/questions_json/1993/00_source/question_1993_1.json
問1 / original_question_id=2779fd82ea2099b3
```

## 作業上の注意

- 現在のワークツリーには、柔道整復師以外の未コミット差分が存在する。今回の作業では戻さず、stage 対象にも含めない。
- 01-04 の patch 本文は、設問単位の目視相当判断を経て作る。機械的な一括分類や既存 patch 流用で完了扱いにしない。
- `output/` は `.gitignore` 対象なので、成果物をコミットする場合は `git add -f` が必要。
