# 柔道整復師 01-04 prompt full pass

## Objective

`output/judoseifukushi/questions_json/` 配下の 1993-2026 年、合計 7,600 問を、`01_prompt_fix_questionType.md` → `02_prompt_fix_questionIntent.md` → `03_prompt_add_explanationText.md` → `04_prompt_link_questionSetId.md` の順で、一問ずつ目視相当で整備し、Firestore 取り込み前の品質に到達させる。

## Original Request

`01~04prompt` の作業を柔道整復師対象で進めたい。合計 7,600 問を一問ずつ確認し、まずは下準備を整えたい。

## Intake Summary

- Input shape: `existing_plan`
- Audience: 柔道整復師の問題データ整備とアプリ利用者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: `7,600問` すべてについて `01` `02` `03` `04` の patch / merge / verification が揃い、`prepare_firestore_upload.py judoseifukushi` の dry-run まで通ること
- Goal oracle: `output/judoseifukushi/questions_json/*/10_questionType_fixed/`, `15_correctChoiceText_fixed/`, `21_explanationText_added/`, `22_questionSetId_linked/` が全 52 出題回で揃い、coverage check / merge / upload-dry-run が一致すること
- Likely misfire: 一部年だけで完了扱いにする、03 の一次情報確認を省く、または merge / upload-dry-run まで到達せずに止めること
- Blind spots considered: `00_source` の解説補強が空の設問、年度ごとの件数差、questionSetId の category 依存、既存 dirty worktree の混在
- Existing plan facts:
  - `00_source` は編集しない
  - `03` で `explanation_common_prefix` / `explanation_common_summary` / `explanation_choice_snippets` が不足する場合は外部 Web の一次情報を使ってよい
  - `01` `02` `04` はローカルファイルのみで判断する
  - 出力は固定ファイル名で上書きし、タイムスタンプ付き patch を増やさない
  - `questionSetId` は `category.json` の `questionSets[].questionSetId` のみ使う
  - 柔道整復師以外の dirty worktree は戻さず stage しない

## Goal Oracle

The oracle for this goal is:

`output/judoseifukushi/questions_json/*` 配下の全 52 出題回について、`01` `02` `03` `04` の成果物が揃い、検証コマンドが通り、件数が 7,600 問で一致していること。`03` は不足時に一次情報補完を使ってもよいが、解説補強の根拠が追えること。

PM は各 task receipt をこの oracle と照合し続ける。途中で一部の slice が終わっても、全体 oracle を満たすまで goal は完了しない。

## Goal Kind

`existing_plan`

## Current Tranche

全 7,600 問を単一の完了対象として `01→02→03→04` の順に整備する。現在は GoalBuddy v2 board への移行と再開準備を整え、次の Worker package を安全に立ち上げられる状態にする。個別の出題回は checkpoint / resume の単位であり、一部の年や一部の出題回だけを完了扱いにしない。

## Non-Negotiable Constraints

- 1問ごとの精度を落とさない
- `01` → `02` → `03` → `04` の順番を守る
- `00_source` を編集しない
- `03` では不足情報がある設問に限り外部 Web の一次情報を補う
- `01` `02` `04` はローカルファイルのみで判断する
- 問題文・選択肢・元解説の突き合わせで判断し、キーワード一致だけの機械的な分類に寄せない
- `questionSetId` は `category.json` の `questionSets[].questionSetId` のみ使う
- 出力ファイルは固定ファイル名で上書きし、タイムスタンプ付き patch を増やさない
- 柔道整復師以外の dirty worktree は戻さず、stage 対象にも含めない

## Execution Order

1. 年単位で `00_source` の件数と問題順を固定する。
2. 01 prompt: `questionType` を `00_source` 基準で確認し、必要なパッチを `10_questionType_fixed/` に固定ファイル名で作る。
3. `scripts/merge/00_merge_all.py <year> --base-dir output/judoseifukushi/questions_json` で `20_merged_1/` を作る。
4. 02 prompt: `questionIntent` を `20_merged_1` 基準で確認し、`15_correctChoiceText_fixed/` に固定ファイル名で作る。
5. 02 後の merge で `correctChoiceText` を補完し、不整合がある場合だけ `23_correctChoiceText_fixed/` を作る。
6. 03 prompt: `explanationText` を `20_merged_1` 基準で作り、`21_explanationText_added/` に固定ファイル名で保存する。
7. 04 prompt: `category.json` の `questionSets[].questionSetId` だけを使い、`22_questionSetId_linked/` に固定ファイル名で保存する。
8. 年単位で merge と検証を通し、全件完了後に資格単位の最終監査を行う。

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

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after one verified Worker package when the broader owner outcome still has safe local follow-up work.

Do not create one Worker/Judge pair per repeated file or repeated source slice. Put repeated same-shape work into one bounded package and review the package as a whole.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

A good task is the largest safe useful slice.

Small is not the goal. Useful is the goal.

A Worker should finish the whole assigned slice. A Judge should judge the whole assigned slice. A PM should reorient the board when tasks are safe but not moving the outcome.

Tiny tasks are allowed when the failure is isolated, the risk is high, the scope is unknown, or the tiny task unlocks a larger slice. Tiny tasks are bad when they keep happening, do not change behavior, only add wrappers/contracts/proof files, or avoid the real milestone.
