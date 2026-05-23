# <資格名> patch 品質向上

## Objective

`output/<qualification_code>/questions_json/<list_group_id>/` 配下の対象設問について、対象資格の専門家・問題作成者・参考書著者の観点で1問ずつ精査し、過去問の各設問に対する正しい回答を `99.99%` 水準まで引き上げる。あわせて、各選択肢の解説が教材として公開できる品質であることを確認し、必要なら `correctChoiceText` と `explanationText` の patch を更新する。

## Original Request

`<qualification_name>` の過去問について、新規作成分と既存再更新分を区別せず、1問ずつ丁寧に見直したい。単に検証スクリプトを通すのではなく、各設問の正しい回答と解説品質を、対象資格の専門家・問題作成者・参考書著者の観点で確認し、必要に応じて検索しつつ、スクリプト自動生成ではなく手作業で品質を上げたい。

## Intake Summary

- Input shape: `existing_plan`
- Audience: `<qualification_name>` の受験者、および日次更新を担当する運用者
- Authority: `requested`
- Proof type: `artifact + verification + review`
- Completion proof: 対象設問が一問ずつ処理され、各設問で正しい回答、`questionIntent`、`answer_result_text`、`correctChoiceText`、`explanationText` の整合が専門家水準で確認され、必要な patch 更新と内容監査に通ること
- Goal oracle: 対象設問ごとに、過去問の正しい回答が `99.99%` 水準で確認され、解説が参考書・公式教材に載せても破綻しない品質に達し、最終監査で `full_outcome_complete: true` を記録すること
- Likely misfire: 既存 patch の流用や機械置換で済ませる、複数問を雑にまとめる、検索結果の丸写しをする、または検証を通さず完了扱いにすること
- Blind spots considered:
  - `questionType` / `correctChoiceText` / `explanationText` は品質基準が異なる
  - `correctChoiceText` は実装上 `questionIntent` と結びつくため、原因切り分けが必要な場合がある
  - `explanationText` は必要なら外部一次情報で裏取りするが、`question_url` の再取得には依存しない
  - 新規 patch と既存 patch 再更新を同一基準で扱う必要がある
  - file-level coverage check は必要条件であり、正答精度と解説品質の十分条件ではない
- Existing plan facts:
  - 会話・報告は日本語で行う
  - 変更時には作業内容と保存先を明示する
  - patch 本文を Python 等で量産しない
  - 1 Worker は 1問だけを扱う

## Goal Oracle

The oracle for this goal is:

`対象設問の全件について、一問ずつの専門家レビュー・必要な裏取り・正しい回答の確認・教材品質の解説確認・必要な patch 更新・coverage check・内容監査が完了し、最終監査 receipt が full_outcome_complete: true を記録する。`

The PM must keep comparing task receipts to this oracle. A finished command, a finished year, or a passing partial check is not enough. The goal finishes only when every queued question has been processed and the final audit maps the receipts and verification back to this oracle.

## Goal Kind

`existing_plan`

## Current Tranche

今回の tranche は、`<qualification_code>` の `<list_group_id_or_range>` にある棚卸し済みの対象設問だけを扱う。順序は `<execution_order>` とし、各設問を独立した Worker slice として 1 問ずつ進める。

## Non-Negotiable Constraints

- 会話・報告は常に日本語で行う。
- 変更時には作業内容と保存先を明示する。
- patch 本文をスクリプトで自動生成しない。スクリプト利用は archive、materialize、check、prepare、差分確認に限る。
- Worker は一般的な目視確認者ではなく、対象資格の専門家・問題作成者・参考書著者として判断する。
- 既存 patch の妥当性を前提にせず、必要なら元情報から再判定する。
- 元ファイル (`00_source` / `20_merged_1` など) は書き換えない。
- `questionType` は `prompt/01_prompt_fix_questionType.md` に従い、`00_source/question_*_*.json` を一次情報の基準にする。`20_merged_1` などは補助参照に限り、外部Web参照は禁止する。
- `questionIntent` は `prompt/02_prompt_fix_questionIntent.md` に従い、`20_merged_1/question_*_merged.json` を一次情報にする。不足時のみ `00_source/question_*_*.json` を最小限参照し、外部Web参照は禁止する。
- `prompt/02_prompt_fix_questionIntent.md` は `questionIntent` 精査用であり、`correctChoiceText` を直接目視判定する prompt として扱わない。
- `correctChoiceText` は `99.99%` を目指す厳密レビュー対象とし、`questionIntent`、`answer_result_text`、`choiceTextList`、元解説、必要なら `15_correctChoiceText_fixed` まで原因を遡って確認する。`questionIntent` が誤っている場合は `15_correctChoiceText_fixed` を先に更新し、その反映後に最終 `correctChoiceText` を確認する。
- `explanationText` は `prompt/03_prompt_add_explanationText.md` に従い、`20_merged_1/question_*_merged.json` を主入力にする。必要時のみ `23_correctChoiceText_fixed/`、`00_source/`、信頼できる外部Web一次情報を参照する。
- `explanationText` は正誤、根拠、誤り箇所、正しい内容、必要な法令・制度・技術根拠を満たすか確認する。外部Webを使う場合も、URL貼り付けや長文転載はしない。
- `explanationText` は、受験者が誤学習せず、参考書の解答解説として公開しても矛盾や曖昧さが残らない品質を満たすか確認する。
- `question_url` の再取得や、問題サイト再参照を判断根拠にしない。
- 新規作成分と既存再更新分を同一の品質基準で扱う。
- 更新対象外の資格、年度、patch file には触れない。
- 既存のユーザー変更や無関係な差分は戻さない。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after a single file, a single patch family, or a single verification pass if queued target questions remain.

Do not collapse multiple queued question-level tasks into an untracked batch.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible.

For this tranche, one Worker slice equals one target question. Each Worker must name the exact question identity, source JSON, relevant patch files, evidence paths, verification command, and stop conditions before editing.

## Canonical Board

Machine truth lives at:

`docs/goals/<slug>/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/<slug>/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Work only on the active board task.
4. Review exactly one target question unless a stop condition is hit.
5. Run the file-level verification listed on that task.
6. Write a compact receipt.
7. Mark that task done, promote the next queued Worker, and continue.
8. Finish only with a final Judge/PM audit that proves all target questions are done and meet the oracle.
