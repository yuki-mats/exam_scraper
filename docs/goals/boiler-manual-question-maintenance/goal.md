# 一級・二級ボイラー技士の一問ずつ目視問題整備

## Objective

一級ボイラー技士（`boiler1`）と二級ボイラー技士（`boiler2`）について、現行成果物と問題整備workflowから未完工程を確定し、各問題を必ず一問ずつ目視して必要なpatch層を整備する。内容判断を自動生成・一括補正・自動レビューへ委ねず、全対象問題の必要工程が完了するまで継続する。

## Original Request

「一級ボイラー、二級ボイラーについて、問題整備ができていない工程を洗い出して、1問ずつ目視で、機械的な作業はせずに、整備作業を進めてほしい。」

## Intake Summary

- Input shape: `specific`
- Audience: 資格試験の受験者と問題整備担当者
- Authority: `requested`
- Proof type: `review`
- Completion proof: 両資格の全対象問題に一問単位の目視receiptがあり、未完工程が残らず、`00_source`不変と公式workflowの完了条件を確認できること。
- Goal oracle: 資格別の未完工程台帳、全対象問題ID、一問単位の目視receipt、`00_source`不変、公式workflowの最終状態が相互に対応していること。
- Likely misfire: 自動生成や一括補正で見かけの進捗だけを埋める、一部の問題だけで全体完了とする、取得欠損調査と問題整備を混同する、又は機械検証に内容判断を代行させること。
- Blind spots considered: 既存runの進捗率やファイル件数だけでは完了を判定できない。二級ボイラーの39問回は欠問調査を分離する。問題由来、正答、法令、解説品質は各正本から独立に確定する。
- Existing plan facts: 対象は`boiler1`と`boiler2`。未完工程を先に洗い出す。内容の確認と整備は一問ずつ目視する。機械的な生成・一括補正・自動レビューは行わない。

## Goal Oracle

The oracle for this goal is:

`boiler1とboiler2の全対象問題について、未完工程台帳と一問単位の目視receiptが対応し、00_source不変のまま公式workflowの完了条件を満たしている`

PMは各問題のreceiptをこのoracleへ継続的に照合する。工程一覧の作成、最初の一問の完了、ファイル件数、進捗率、又は単独のvalidator成功だけでは完了しない。最終Judge又はPM監査が全対象ID、各工程、目視根拠、保存先、read-only検証を対応付け、`full_outcome_complete: true`を記録したときだけ完了とする。

## Goal Kind

`specific`

## Current Tranche

まず運用正本、workflow定義、現行manifest・progress・patch層・source台帳を読み、資格別の未完工程と全対象問題IDを確定する。次に最初の一問を選び、その一問の原文、選択肢、正答、必要な根拠、既存patchを人が直接読み、必要な工程だけを責務に合うpatch層へ保存する。同じ手順を必ず一問ずつ繰り返し、両資格の全対象問題を閉じる。

各問題の整備は小さく見えても、ユーザーが指定した安全境界そのものである。複数問題を一つのWorker変更へまとめない。反復時はPMが次の一問を選び、新しいWorker taskを追加する。

## Non-Negotiable Constraints

- `00_source`の内容・ファイル名を変更又は削除しない。
- 自動生成、一括補正、bulk rewrite、scriptによるpatch生成、自動レビューを行わない。
- 内容の正誤や文章品質をvalidator、件数、進捗率だけで決めない。各問題を一問ずつ目視する。
- 読み取り専用の機械検証は、目視判断後の不整合検出と`00_source`保護にだけ使い、正しい内容を決め打ちしない。
- 問題由来、正答、法令根拠、解説品質は、それぞれ責務に合う正本から独立に確定し、最後に整合性を確認する。
- 人間向け文章は初学者が一度で理解できる日本語へ推敲し、`prompt/03_prompt_add_explanationText.md`の問題解説固有契約を守る。
- 既存Firestore IDを維持する。Firestore公開は問題整備完了と混同せず、別の明示的な外部反映境界として扱う。
- 変更は責務に応じたpatch層へ限定し、無関係な資格、問題、工程を変更しない。
- Gitは`main`のみを使い、関連変更ごとに検証・commit・`origin/main`へのpush・readbackを行う。

## Stop Rule

最終監査が元の依頼全体の完了を証明した場合だけ停止する。

未完工程一覧の作成、最初の一問の選定、又は一問の整備だけでは停止しない。安全に次の一問へ進める場合は、PMが次の一問単位のWorker taskを追加して継続する。

公式資料が確認できない、source conflictがある、正答又は法令根拠を確定できない問題は、推測で埋めない。その一問を`hold`として根拠と必要な判断をreceiptへ残し、他の安全な問題へ進む。

## Slice Sizing

このGoalでは、一つのWorker taskが扱う内容変更は必ず一問だけとする。一問の中では、必要な工程を分断せず、原文確認から責務別patch、文章推敲、read-only検証までを一つの縦切りsliceとして閉じる。

Scoutは全体の未完工程をread-onlyで棚卸しできる。Judgeは最初の一問と安全境界を決める。Workerは指定された一問以外を変更しない。PMはreceiptを受けて次の一問を追加する。

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.3/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/boiler-manual-question-maintenance
```

ローカルボードが動いている場合は、`state.yaml`とライブボードの表示を照合する。

## Canonical Board

Machine truth lives at:

`docs/goals/boiler-manual-question-maintenance/state.yaml`

## Run Command

```text
Codex: /goal Follow docs/goals/boiler-manual-question-maintenance/goal.md.
Claude Code: /goalbuddy Follow docs/goals/boiler-manual-question-maintenance/goal.md.
```

## PM Loop

各`/goal`継続時は、GoalBuddyの`references/goal-execution.md`、このcharter、`state.yaml`の順に読み、active taskだけを実行する。各問題の完了時に、確認したsource ID、未完だった工程、読んだ根拠、判断、変更ファイル、read-only検証、Git commit/push/readbackをreceiptへ残す。

Scout/Judge/Worker taskは必ず役割どおりに扱い、PMだけがboard状態と次の一問を選ぶ。最終的に両資格の全対象IDとreceiptを照合し、未完工程、未記録問題、source変更、未push変更がないことを監査する。
