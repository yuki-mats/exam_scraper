# 柔道整復師 01-04 prompt full pass

## Objective

`output/judoseifukushi/questions_json/` 配下の 1993-2026 年、合計 7,600 問について、01-04 prompt の作業を一問ずつ目視相当で実施し、Firestore 取り込み前の品質に到達させる。

## Scope

- qualification: `judoseifukushi`
- qualificationName: `柔道整復師`
- years: `1993-2026`
- total questions: `7,600`
- source of truth: `output/judoseifukushi/questions_json/<year>/00_source/question_<year>_<n>.json`
- category source: `output/judoseifukushi/category/category.json`
- taxonomy basis: 柔道整復研修試験財団の公式出題基準に基づく既存 `category.json`

## Required Patch Families

1. `10_questionType_fixed`
2. `15_correctChoiceText_fixed`
3. `21_explanationText_added`
4. `22_questionSetId_linked`

`23_correctChoiceText_fixed` は、`answer_result_text` と `questionIntent` からの自動補完後に、実正答と `correctChoiceText` の不整合が残る場合だけ使う。

## Execution Order

1. 年単位で `00_source` の件数と問題順を固定する。
2. 01 prompt: `questionType` を `00_source` 基準で確認し、必要なパッチを `10_questionType_fixed/` に固定ファイル名で作る。
3. `scripts/merge/00_merge_all.py <year> --base-dir output/judoseifukushi/questions_json` で `20_merged_1/` を作る。
4. 02 prompt: `questionIntent` を `20_merged_1` 基準で確認し、`15_correctChoiceText_fixed/` に固定ファイル名で作る。
5. 02 後の merge で `correctChoiceText` を補完し、不整合がある場合だけ `23_correctChoiceText_fixed/` を作る。
6. 03 prompt: `explanationText` を `20_merged_1` 基準で作り、`21_explanationText_added/` に固定ファイル名で保存する。
7. 04 prompt: `category.json` の `questionSets[].questionSetId` だけを使い、`22_questionSetId_linked/` に固定ファイル名で保存する。
8. 年単位で merge と検証を通し、全件完了後に資格単位の最終監査を行う。

## Non-Negotiable Constraints

- 会話と報告は日本語で行う。
- `00_source` は編集しない。
- `00_source` に `explanation_common_prefix` / `explanation_common_summary` / `explanation_choice_snippets` がない、または不足する設問は、03 prompt の解説補強として外部Webの一次情報を参照してよい。`question_url` の再取得や問題サイト依存はしない。
- 01、02、04 prompt はローカルファイルだけで判断する。
- 出力ファイルは固定ファイル名で上書きし、タイムスタンプ付き patch を増やさない。
- `questionSetId` は `category.json` の `questionSets[].questionSetId` のみ使う。`folderId` を設問に直接付与しない。
- 現在のワークツリーには他資格の未コミット差分があるため、柔道整復師以外の差分を戻さず、stage しない。

## Verification Commands

```bash
python3 scripts/check/check_questiontype_patch_coverage.py \
  --source output/judoseifukushi/questions_json/<year>/00_source/question_<year>_<n>.json \
  --patch output/judoseifukushi/questions_json/<year>/10_questionType_fixed/question_<year>_<n>_questionType_fixed.json
```

```bash
python3 scripts/check/check_correct_choice_patch_coverage.py \
  --source output/judoseifukushi/questions_json/<year>/20_merged_1/question_<year>_<n>_merged.json \
  --patch output/judoseifukushi/questions_json/<year>/23_correctChoiceText_fixed/question_<year>_<n>_correctChoiceText_fixed.json \
  --require-full \
  --require-snippets \
  --require-change-meta
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

```bash
python3 scripts/check/check_questionSetId.py \
  --category output/judoseifukushi/category/category.json \
  --original output/judoseifukushi/questions_json/<year>/20_merged_1/question_<year>_<n>_merged.json \
  --fixed output/judoseifukushi/questions_json/<year>/30_merged_2/<latest merged file> \
  --compare-count \
  --questionset-only
```

## Completion Proof

- `queue.jsonl` の 7,600 行がすべて処理済み。
- 各年で必要 patch family の coverage check が全ファイル終了コード 0。
- 各年で `scripts/merge/00_merge_all.py` が成功し、`30_merged_2/` に `questionSetId` が入っている。
- `prepare_firestore_upload.py judoseifukushi` の dry-run が通る。
- 最終 receipt に `full_outcome_complete: true` を記録する。
