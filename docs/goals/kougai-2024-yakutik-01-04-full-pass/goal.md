# 公害防止管理者 2024 yaku-tik 01-04 full pass

## Objective

`output/kougai/questions_json/2024/00_source/` を yaku-tik 由来だけの canonical source に整備し、公害防止管理者 2024 年度の 135 問を `01_prompt_fix_questionType.md` → `02_prompt_fix_questionIntent.md` → `03_prompt_add_explanationText.md` → `04_prompt_link_questionSetId.md` の順で、一問一問目視クオリティで整備できる状態にする。

最終成果は、135 問すべてについて固定名 patch、merge 済み成果物、review ledger、検証 receipt が揃い、Firestore upload 前の dry-run readiness まで到達していること。

## Original Request

`00_source配下をyaku-tikのみにして、全ての問題を一問一問目視クオリティで01~04プロンプトの作業を進められるようにgoal計画を作成してほしい。`

## Intake Summary

- Input shape: `existing_plan`
- Audience: 公害防止管理者の問題データ整備とアプリ利用者
- Authority: `requested`
- Proof type: `artifact + review + test`
- Completion proof: 2024 年度の canonical `00_source` が yaku-tik 6ファイル・135問だけになり、01〜04 の固定名 patch、`20_merged_1`、`30_merged_2`、manual review ledger、検証結果が全 135 問で揃うこと
- Goal oracle: `output/kougai/questions_json/2024/00_source/` に yaku-tik 以外の source file が残らず、`10_questionType_fixed/`、`15_correctChoiceText_fixed/`、`21_explanationText_added/`、`22_questionSetId_linked/` の全 stage が review ledger と coverage check で 135 問一致すること
- Likely misfire: zoron / qualification-text 由来の重複 source を canonical `00_source` に混ぜたまま進める、bulk 自動生成だけで「目視クオリティ」と扱う、穴埋め問題を根拠なく別 questionType に寄せる、`category.json` にない `questionSetId` を作る、merge / dry-run なしで完了扱いにする
- Blind spots considered:
  - 現在の 2024 `00_source` には yaku-tik 6ファイル 135問に加え、zoron 3ファイル 75問が残っている
  - yaku-tik の穴埋め形式は、選択肢語句ごとの true / false 問題へ変換されている可能性が高い
  - 03 の解説補強は yaku-tik の本文・解説を第一根拠にし、不足時だけ一次情報に寄せる
  - 04 は公害防止管理者向け `category.json` / qualification docs が未整備なら先に整備が必要
  - `output/` は ignore 対象が多く、必要成果物の Git 管理は明示的に範囲確認してから行う
- Existing plan facts:
  - 2024 年度は yaku-tik だけを canonical source にする
  - multi-source scrape の raw evidence は消さず、退避先を作ってから canonicalize する
  - 01〜04 は固定ファイル名で上書きし、タイムスタンプ付き patch を増やさない
  - 1問ごとに source 本文、設問文、選択肢、正誤、解説を照合して review ledger に残す
  - `questionSetId` は `category.json` の `questionSets[].questionSetId` のみ使う
  - Firestore への live upload はこの goal の自動完了条件に含めず、dry-run readiness までに止める

## Goal Oracle

The oracle for this goal is:

2024 年度の canonical `00_source` が yaku-tik 由来の `question_2024_yakutik_1.json`〜`question_2024_yakutik_6.json` だけで構成され、合計 135 問であること。その 135 問すべてについて 01〜04 の固定名 patch、review ledger の完了印、coverage check、merge、upload dry-run readiness の receipt が残っていること。

PM は各 task receipt をこの oracle と照合し続ける。`00_source` の canonical 化だけ、または一部 stage の完了だけでは goal 完了にしない。

## Goal Kind

`existing_plan`

## Current Tranche

対象は 2024 年度 yaku-tik 由来 135 問に限定する。最初の tranche は、現行 `00_source` の 9ファイル状態を raw evidence として保全し、canonical `00_source` を yaku-tik 6ファイルに絞ったうえで、01〜04 の manual review ledger と検証導線を作るところまで。

## Non-Negotiable Constraints

- 常に日本語で報告する
- 変更時には作業内容と保存先を明示する
- canonical `00_source` は 2024 yaku-tik の 6ファイル・135問だけにする
- yaku-tik 以外の raw scrape 成果物は削除せず、退避先か audit note に残す
- 1つの公式問題を複数 source doc として重複投入しない
- 01〜04 は一問一問の本文・選択肢・正誤・解説照合を前提にし、bulk 生成のみで完了扱いにしない
- 01 → 02 → 03 → 04 の順番を守る
- `01` `02` `04` はローカルの canonical source / merge / category を主根拠にする
- `03` は yaku-tik の解説を第一根拠にし、不足時のみ信頼できる一次情報を補助根拠にする
- `questionSetId` は `category.json` の `questionSets[].questionSetId` のみ使う
- 出力は固定ファイル名で上書きし、タイムスタンプ付き patch を増やさない
- Firestore live upload は実行しない。必要なら別途ユーザー承認を取る
- 既存の unrelated dirty worktree は戻さず、stage 対象にも含めない

## Execution Order

1. 現在の 2024 `00_source` を audit し、yaku-tik 6ファイル 135問、zoron 3ファイル 75問の状態を receipt に残す。
2. raw evidence の退避先を作り、canonical `00_source` を yaku-tik 6ファイルだけにする。
3. 2024 yaku-tik 135問に対する manual review ledger を作成し、stage 01〜04 の status を追跡できるようにする。
4. 公害防止管理者向け qualification docs / `category.json` が不足していれば、03/04 の前提として整備する。
5. 01 prompt: `questionType` を canonical `00_source` 基準で確認し、必要な patch を `10_questionType_fixed/` に固定ファイル名で作る。
6. `scripts/merge/00_merge_all.py 2024 --base-dir output/kougai/questions_json` で `20_merged_1/` を更新する。
7. 02 prompt: `questionIntent` と choice-level 正誤の整合を `20_merged_1` 基準で確認し、`15_correctChoiceText_fixed/` に固定ファイル名で作る。
8. 03 prompt: `explanationText` / suggested 系を一問ずつ確認し、`21_explanationText_added/` に固定ファイル名で保存する。
9. 04 prompt: `category.json` に存在する `questionSetId` だけを使い、`22_questionSetId_linked/` に固定ファイル名で保存する。
10. 最終 merge と検証を実行し、135 問すべてが review ledger と一致することを Judge / PM が監査する。

## Verification Commands

```bash
jq -s '[.[].question_bodies | length] | {files:length,total:add,per_file:.}' \
  output/kougai/questions_json/2024/00_source/question_2024_yakutik_*.json
```

```bash
find output/kougai/questions_json/2024/00_source -maxdepth 1 -type f \
  \( -name '*zoron*' -o -name '*qualification*' -o -name '*qtext*' \) -print
```

```bash
.venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check \
  output/kougai/review/01_04_manual_review/kougai_2024_yakutik_01_04_manual_review.jsonl \
  --expected-total 135 \
  --category output/kougai/category/category.json \
  --allow-pending
```

```bash
.venv/bin/python scripts/merge/00_merge_all.py 2024 \
  --base-dir output/kougai/questions_json
```

```bash
.venv/bin/python scripts/check/check_questiontype_patch_coverage.py \
  --source output/kougai/questions_json/2024/00_source/question_2024_yakutik_<n>.json \
  --patch output/kougai/questions_json/2024/10_questionType_fixed/question_2024_yakutik_<n>_questionType_fixed.json
```

```bash
.venv/bin/python scripts/check/check_correct_choice_patch_coverage.py \
  --source output/kougai/questions_json/2024/20_merged_1/question_2024_yakutik_<n>_merged.json \
  --patch output/kougai/questions_json/2024/15_correctChoiceText_fixed/question_2024_yakutik_<n>_correctChoiceText_fixed.json \
  --require-full \
  --require-snippets \
  --require-change-meta
```

```bash
.venv/bin/python scripts/check/check_explanation_patch_coverage.py \
  --source output/kougai/questions_json/2024/20_merged_1/question_2024_yakutik_<n>_merged.json \
  --patch output/kougai/questions_json/2024/21_explanationText_added/question_2024_yakutik_<n>_explanationText_added.json \
  --require-law-grounded-flag
```

```bash
.venv/bin/python scripts/check/check_question_set_patch_coverage.py \
  --source output/kougai/questions_json/2024/20_merged_1/question_2024_yakutik_<n>_merged.json \
  --patch output/kougai/questions_json/2024/22_questionSetId_linked/question_2024_yakutik_<n>_questionSetId_linked.json \
  --category output/kougai/category/category.json \
  --questionset-only
```

```bash
.venv/bin/python scripts/check/check_questionSetId.py \
  --category output/kougai/category/category.json \
  --original output/kougai/questions_json/2024/00_source/question_2024_yakutik_<n>.json \
  --fixed output/kougai/questions_json/2024/30_merged_2/<latest merged file> \
  --compare-count \
  --questionset-only
```

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, source canonicalization, ledger generation, or one verified stage if a safe next Worker task can be activated.

Do not create one Worker/Judge pair per repeated file unless the risk is high or the review ledger requires a single-question handoff. Prefer a bounded stage package, but every question inside the package must still receive one-by-one review.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

For content review, one Worker package may cover a source file or a small contiguous question range only if the package receipt proves that each question was reviewed individually.

A Worker should finish the whole assigned slice. A Judge should judge the whole assigned slice. A PM should reorient the board when tasks are safe but not moving the outcome.

## Canonical Board

`docs/goals/kougai-2024-yakutik-01-04-full-pass/state.yaml`

## Run Command

`/goal Follow docs/goals/kougai-2024-yakutik-01-04-full-pass/goal.md.`

## PM Loop

1. Keep exactly one active task.
2. Do not mark the goal complete while queued required Worker tasks remain.
3. After every Worker package, run the listed verification commands and record the receipt.
4. If a source / answer / category ambiguity appears, write it to `99_model_review_flags/` or the review ledger instead of guessing.
5. Final completion requires a Judge or PM audit that maps receipts back to the Goal Oracle.
