# 医師国家試験 01→04 通し整備

## Objective

医師国家試験の過去問 13,060 問を、`01_prompt_fix_questionType.md` → `02_prompt_fix_questionIntent.md` → `03_prompt_add_explanationText.md` → `04_prompt_link_questionSetId.md` の順で、各問の精度を落とさずに最後まで整備する。

## Original Request

プロンプトに従って `01→02→03→04` の通しで、医師国家試験の過去問を一問ごとに精度を落とさず整備してほしい。

## Intake Summary

- Input shape: `existing_plan`
- Audience: 医師国家試験の問題データ整備とアプリ利用者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: `13,060問` すべてについて `01` `02` `03` `04` の整備が完了し、出題回単位の成果物と検証結果が揃っていること。`02` 後の `correctChoiceText` 導出結果も監査し、必要な設問は `23_correctChoiceText_fixed/` を作る
- Goal oracle: `output/mecnet-kokushi/questions_json/*/10_questionType_fixed/`, `15_correctChoiceText_fixed/`, `21_explanationText_added/`, `22_questionSetId_linked/` が全出題回で揃い、件数・順序・coverage check が一致していること。`03` は `--require-law-grounded-flag` 付きで通す
- Likely misfire: 一部出題回だけを完成扱いにする、解説品質を落とす、またはパッチ作成で止めて merge/検証まで到達しないこと
- Blind spots considered: 出題回ごとの分量差、医療制度・法令問題の混在、`lawGroundedExplanationNotNeeded` の保守的判定、長時間実行時の checkpoint/resume
- Existing plan facts: `mecnet-kokushi` は `13,060問` で inventory と `00_source` が一致済み。`category.json` は MHLW ブループリント命名で整備済み。`03` では `lawGroundedExplanationNotNeeded` を追加済み。作業順は `01` `02` `03` `04` を厳守する

## Goal Oracle

The oracle for this goal is:

`output/mecnet-kokushi/questions_json/*` 配下の全 52 出題回について、`01` `02` `03` `04` の成果物が揃い、検証コマンドが通り、件数が 13,060 問で一致していること。`03` は `lawGroundedExplanationNotNeeded` 全件必須で検証し、`lawReferences` 非空との矛盾がないこと`

PM は各 task receipt をこの oracle と照合し続ける。途中で一部の slice が終わっても、全体 oracle を満たすまで goal は完了しない。

## Progress Baseline

2026-06-09 時点の残数基準は次の通り。

- 最終目標である `01→02→03→04` 全完了ベースの残数: `13,060問`
  - まだ全工程を通し切って完了証跡がある設問は `0問`
- `03` の目視作業 checkpoint ベースの残数: `13,052問`
  - `95回` の先頭 `8問` だけ、`03` の本文・補足質問・`lawGroundedExplanationNotNeeded` を目視相当でレビュー済み

以後の作業は、この残数を減らしていくこと自体を中間目的にせず、最終的に `13,060問` すべてを高精度で漏れなく `01→04` 完了させることを目的に進める。

## Goal Kind

`existing_plan`

## Current Tranche

全 52 出題回・13,060 問を単一の完了対象として `01→02→03→04` の順に整備する。個別の出題回は checkpoint / resume の単位であり、95 回など一部の出題回だけを pilot 完了として区切らない。各出題回で進捗証跡を残しつつ、`03` は解説品質と法令判定を最優先にする。

## Non-Negotiable Constraints

- 1問ごとの精度を落とさない
- `01` → `02` → `03` → `04` の順番を守る
- 問題文・選択肢・元解説の突き合わせで判断し、キーワード一致だけの機械的な分類に寄せない
- `03` では曖昧な法令解釈を `true` にしない
- `03` の coverage check は `--require-law-grounded-flag` を必ず付ける
- `02` 後の `correctChoiceText` 導出結果は、設問意図・正解番号・選択肢・元解説の突き合わせで監査する。疑義がある場合は `23_correctChoiceText_fixed/` を作ってから `03` に進む
- MHLW ブループリントの命名をカテゴリと質問セット名にそのまま使う
- 出題回単位で checkpoint / resume 可能にする
- 95 回などの個別出題回は中間 checkpoint であり、active task や完了判定を単一出題回に閉じない
- 途中の pilot 完了を全体完了扱いにしない

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or a single verified pilot slice if the broader owner outcome still has safe local follow-up work.
Do not stop after one verified Worker package when the broader owner outcome still has safe local follow-up work.
Do not create one Worker/Judge pair per repeated file or repeated exam occurrence. Put repeated same-shape work into one bounded package and review the package as a whole.
