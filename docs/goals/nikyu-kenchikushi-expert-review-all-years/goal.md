# 二級建築士 全年度(2015-2025) 専門家レビュー

## Objective

`output/2nd-class-kenchikushi/questions_json/85001` 〜 `85011`（2015〜2025 年度）の全設問について、対象資格の専門家・問題作成者・参考書著者の観点で 1 問ずつ精査し、`correctChoiceText` を 99.99% 水準で確認する。あわせて、対応する `21_explanationText_added/` の `explanationText` が教材として公開できる品質であることを確認し、必要なら patch を更新する。

## Scope Notes

- `list_group_id=850003` は `85001..85011` の学科Ⅲ（建築構造）と `original_question_id` が 100% 重複しているため、本 goal では重複レビューを避ける目的で対象外とする。

## Original Request

二級建築士に対して goal 目標を立てて実行したい。対象は単なる人間の目視ではなく、その資格の専門家、問題作成者、参考書作成者を想定する。`correctChoiceText` と解説品質を一問ずつ丁寧に確認し、必要があれば検索しながら精度を上げる。

## Intake Summary

- Input shape: `existing_plan`
- Audience: 二級建築士試験の受験者、および日次更新を担当する運用者
- Authority: `requested`
- Proof type: `artifact + verification + review`
- Completion proof: 対象 25 設問が一問ずつ処理され、各設問で `questionIntent`、`answer_result_text`、`correctChoiceText`、`explanationText` の整合が専門家水準で確認され、必要な patch 更新と内容監査に通ること
- Goal oracle: 対象設問ごとに、正しい回答が 99.99% 水準で確認され、解説が参考書・公式教材に載せても破綻しない品質に達し、最終監査で `full_outcome_complete: true` を記録すること
- Likely misfire: 既存 patch の流用、正答番号だけの機械確認、複数問の雑な一括更新、法令・歴史・環境工学などの根拠を推測で書くこと
- Blind spots considered:
  - 既存の `improve-nikyu-kenchikushi-explanationtext` goal は explanation 専用であり、今回の `correctChoiceText` 99.99% レビューとは範囲が違う
  - `85010` 配下には `23_correctChoiceText_fixed/` が未作成で、必要時だけ最終 correctChoiceText patch を作る
  - `question_85010_1_merged.json` は 25 設問あり、1 Worker = 1 設問で進める
  - file-level check は必要条件であり、専門家水準の正答・解説品質の十分条件ではない

## Goal Oracle

The oracle for this goal is:

`2015〜2025 年度（list_group_id=85001..85011）の全設問について、一問ずつの専門家レビュー、必要な裏取り、正しい回答の確認、教材品質の解説確認、必要な patch 更新、coverage check、内容監査が完了し、最終監査 receipt が full_outcome_complete: true を記録する。`

The PM must keep comparing task receipts to this oracle. A finished command, a single passing file, or a partial review is not enough. The goal finishes only when every queued question has been processed and the final audit maps receipts and verification back to this oracle.

## Goal Kind

`existing_plan`

## Current Tranche

この goal は全年度（2015〜2025）を対象とするが、最初の tranche は安全のため `85010/20_merged_1/question_85010_1_merged.json` の 25 設問から開始する。以後は同年度の残り merged file を消化し、次年度へ進む。

## Non-Negotiable Constraints

- 会話・報告は常に日本語で行う。
- 変更時には作業内容と保存先を明示する。
- Worker は一般的な目視確認者ではなく、二級建築士の専門家・問題作成者・参考書著者として判断する。
- patch 本文をスクリプトで自動生成しない。スクリプト利用は件数確認、差分確認、check、prepare に限る。
- 元ファイル (`00_source` / `20_merged_1`) は書き換えない。
- `questionIntent` は各年度の `20_merged_1/*.json` を一次情報にし、不足時のみ `00_source/` を最小限参照する。
- `correctChoiceText` は `questionIntent`、`answer_result_text`、`choiceTextList`、元解説を突き合わせて確認する。
- `explanationText` は `prompt/03_prompt_add_explanationText.md` に従い、受験者が誤学習せず、参考書の解答解説として公開しても矛盾や曖昧さが残らない品質を満たすか確認する。
- 法令、歴史的建築、設計者、数値、定義を断定する場合は、必要に応じて信頼できる一次情報または権威ある情報で裏取りする。
- `question_url` の再取得や、問題サイト再参照を判断根拠にしない。
- 更新対象外の資格の data tree には触れない。
- 既存のユーザー変更や無関係な差分は戻さない。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after a single question, a single verification pass, or a partial patch if queued target questions remain.

Do not collapse multiple queued question-level tasks into an untracked batch.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible.

For this tranche, one Worker slice equals one target question. Each Worker must name the exact `original_question_id`, source JSON, relevant patch files, evidence paths, verification command, and stop conditions before editing.

## Canonical Board

Machine truth lives at:

`docs/goals/nikyu-kenchikushi-expert-review-all-years/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/nikyu-kenchikushi-expert-review-all-years/goal.md.
```

## PM Loop

1. Read this charter.
2. Read `state.yaml`.
3. Work only on the active board task.
4. Review exactly one target question unless a stop condition is hit.
5. Run the listed verification.
6. Write a compact receipt.
7. Mark that task done, promote the next queued Worker, and continue.
8. Finish only with a final Judge/PM audit that proves 全年度の対象設問がすべて done で oracle を満たしている。
