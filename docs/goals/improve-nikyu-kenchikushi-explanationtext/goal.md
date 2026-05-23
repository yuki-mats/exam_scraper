# 二級建築士 explanationText パッチ品質底上げ

## Objective

`output/2nd-class-kenchikushi/questions_json/85002` から `85011` までの `21_explanationText_added/` 配下について、更新対象として棚卸し済みの 32 問を一問ずつ改善し、受験者が各選択肢の正誤理由と判断根拠に納得できる `explanationText` へ引き上げる。

## Original Request

二級建築士の過去問について、`21_explanationText_added/` 配下のパッチファイルをより良い解説文に更新したい。まず更新対象を棚卸しし、その後は `85002` から `85011` まで一問ずつ進めたい。`prompt/03_prompt_add_explanationText.md` と `goalbuddy:goal-prep` を使う。

## Intake Summary

- Input shape: `existing_plan`
- Audience: 二級建築士試験の受験者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: 棚卸し済みの 32 ファイルが一問ずつ更新され、各更新ファイルで `prompt/03_prompt_add_explanationText.md` の品質定義、法令条項明記ルール、配列長整合、既知の薄い説明除去条件を満たすこと。
- Goal oracle: 各更新対象パッチについて、各選択肢の `explanationText` が `正しい。` または `間違い。` で始まり、正しい理由または誤り箇所・誤り理由・正しい内容が具体化され、法令問題では受験者が確認可能な法令名・条項が明記され、個別 coverage 検証と品質監査に通ること。
- Likely misfire: 年度全体をまとめて雑に更新する、誤り選択肢で誤り箇所が曖昧なままにする、法令問題で条項を省略する、または根拠の薄い定型句を少し言い換えただけで完了扱いにすること。
- Blind spots considered: 年度漏れ、更新対象外ファイルへの誤着手、法令条項の裏取り不足、Web 検索への依存過多、選択肢本文の言い換え止まり、既存パッチとの衝突。
- Existing plan facts:
  - `85001` は更新済みで、現 tranche から除外する。
  - 対象は棚卸し済みの 32 ファイルに限定し、`85002` から `85011` の順で一問ずつ進める。
  - 品質基準は `/Users/yuki/development/exam_scraper/prompt/03_prompt_add_explanationText.md` を使う。
  - 実行時はローカル成果物を優先しつつ、底上げの裏取り目的に限って Web 検索を使ってよい。
  - 法令問題では、確認できた法令名・条・項・号を `explanationText` に明記する。
  - 会話・報告は日本語で行い、変更時には作業内容と保存先を明示する。

## Goal Oracle

The oracle for this goal is:

`棚卸し済み 32 ファイルの explanationText 更新が完了し、各ファイルで配列長整合・薄い定型句除去・正誤根拠の具体化・法令条項明記ルールが確認され、最終監査が full_outcome_complete: true を記録する。`

The PM must keep comparing task receipts to this oracle. A finished year, a single passing file, or a partially improved batch is not enough. The goal finishes only when all 32 queued question-level tasks are done and the final audit maps the receipts and verification back to this oracle.

## Goal Kind

`existing_plan`

## Current Tranche

今回の tranche は、棚卸し済みの更新対象 32 ファイルだけを扱う。順序は `85002` -> `85003` -> `85004` -> `85005` -> `85006` -> `85007` -> `85008` -> `85009` -> `85010` -> `85011` とし、各 `question_*_merged` パッチを一問ずつ独立した Worker スライスとして進める。最初の `/goal` 実行では、最初の active Worker だけを処理し、その後 PM が次の queued Worker を active に繰り上げる。

## Non-Negotiable Constraints

- 会話・報告は常に日本語で行う。
- 品質基準は `/Users/yuki/development/exam_scraper/prompt/03_prompt_add_explanationText.md` を優先する。
- ローカル成果物を優先し、同一 `list_group_id` 配下の `20_merged_1`、`00_source/`、`23_correctChoiceText_fixed/` を先に確認する。
- Web 検索は、ローカルだけでは裏取りが弱い場合の補助に限る。一次情報・公式資料を優先し、転載や長文引用はしない。
- 法令問題では、確認できた法令名・条・項・号を `explanationText` に明記する。確認できない条項番号や数値は推測で書かない。
- `explanationText` 本文をスクリプトで量産しない。スクリプト利用は検証、件数確認、差分監査の補助に限る。
- `20_merged_1` の元 JSON は書き換えない。
- 正しい選択肢でも理由を書く。間違いの選択肢では、誤っている語句・条件・数値・関係を明示し、正しい内容を書く。
- `設問の通りです`、`記述は正しいです`、`記述は誤りです`、`正解です`、空欄の `「」`、学習指導コメントを残さない。
- 更新対象外の年度・ファイルには触れない。
- 変更時には作業内容と保存先を明示する。
- 既存のユーザー変更や無関係な差分は戻さない。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after a single file, a single year, or a single verification pass if queued target files remain.

Do not collapse multiple queued question-level tasks into an untracked batch. The user explicitly requested one question at a time.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible.

For this tranche, one Worker slice equals one target file. Each Worker must name the exact patch file, corresponding source JSON, local evidence directories, verification command, and stop conditions before editing.

## Canonical Board

Machine truth lives at:

`docs/goals/improve-nikyu-kenchikushi-explanationtext/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/improve-nikyu-kenchikushi-explanationtext/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Work only on the active board task.
4. Update exactly one target file unless a stop condition is hit.
5. Run the file-level verification listed on that task.
6. Write a compact receipt.
7. Mark that task done, promote the next queued Worker, and continue.
8. Finish only with a final Judge/PM audit that proves all 32 target files are done and meet the oracle.
