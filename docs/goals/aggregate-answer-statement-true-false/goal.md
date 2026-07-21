# 集約回答型過去問の記述単位正誤化

## Objective

「正しい記述はいくつあるか」「正しい組合せはどれか」など、問題内の複数記述をまとめて回答させる過去問を、各記述について直接「正しい／間違い」を回答できる資格共通の問題へ安全に変換する。

第一種衛生管理者の全年度・全問題を一次開発の試行対象とし、検証を通った派生問題だけを同資格に限定して本番公開する。

## Original Request

問題文内に複数の記述がある集約回答型の過去問では、学習者が次回の学習時にどの記述を間違えたか分からない。文字列を一から生成せず、ツールで原文から機械的に抜き出し、エージェントが位置をレビューして、記述単位の正誤問題として学べるようにする。

## Intake Summary

- Input shape: `existing_plan`
- Audience: 過去問を記述単位で復習する学習者
- Authority: `approved`
- Proof type: `test`
- Completion proof: 資格共通実装と第一種衛生管理者の全件試行が、原文不変、二者一致、正誤確定、派生ID、変換、公開前gate、限定公開readbackまで再現可能に通る
- Goal oracle: 第一種衛生管理者の全年度・全問題を走査したmanifestとreceiptが、対象・対象外・保留・公開件数を説明し、生成された全派生問題が原文spanへ逆引きできる
- Likely misfire: A〜Dなど一部の表記だけに対応して完了とする、エージェント生成文を保存する、元集約問題も重複出題する、一部記述だけを公開する、又は一次開発で表示UIまで広げる
- Blind spots considered: 抽出形式の揺れ、機械候補の見落とし、集約正答から個別正誤を確定できない問題、原文hash変化、既存IDへの誤った履歴継承、既存dirty変更との混線
- Existing plan facts: `state.yaml` の `goal.intake.existing_plan_facts` を正本とする

## Goal Oracle

The oracle for this goal is:

`第一種衛生管理者の全件trialで、全問題の対象分類、対象問題の全記述span、二者一致結果、機械検証、既存工程による正誤確定、派生問題、保留理由、00_source不変、公開前dry-run、限定公開後readbackを一つの追跡可能な証拠列として確認できること。`

計画、候補抽出だけの成功、少数サンプルの成功では完了しない。最終Judge又はPM監査がこのoracleへ全receiptを対応付け、`full_outcome_complete: true` を記録した場合だけ完了とする。

## Goal Kind

`existing_plan`

## Current Tranche

1. 合意仕様を既存の責務に合う正本へ統合する。
2. 現行schema、merge、convert、問題整備システム、既存問題報告機能、公開フローとの接続点を確認する。
3. 資格共通の対象分類、span候補抽出、二者レビュー契約、機械検証、全記述単位の派生ID生成を実装する。
4. 正誤と解説は既存工程に接続し、新しい重複工程を作らない。
5. 第一種衛生管理者の全件trialを実行し、対象・対象外・保留を記録する。
6. 同じ元問題の全記述が確定した問題だけを公開候補にする。
7. 公開前gateを通し、第一種衛生管理者だけを本番公開してreadbackする。

## Non-Negotiable Constraints

- `00_source`の内容、ファイル名、IDを手作業・AI・patch工程で変更しない。
- 対象は文字表記ではなく、「元回答ではどの記述を誤ったか分からない集約回答型問題」という意味で定義する。
- 元問題で一項目になっている範囲を1記述とし、項目内をさらに分割しない。
- 抽出文字列はツールが原文spanから作る。エージェント出力から文章を保存する経路を作らない。
- エージェント出力はsource hash、対象判定、start/end、approve/hold、定型issue codeに限定する。
- 2つの独立レビュー結果が完全一致し、機械検証を通った場合だけ確定する。不一致は裁定せず保留する。
- 機械候補だけでなく、第一種衛生管理者の全問題を同じ2レビューで対象・対象外・保留に分類する。
- 同じ元問題の全記述が抽出・正誤確定できた場合だけ、まとめて公開する。
- 正誤・解説には既存の問題整備工程を使い、専用の重複工程を追加しない。
- 派生問題は新しい安定IDを使い、元問題との対応は`originalQuestionId`で保持する。意味の異なる既存IDへ学習履歴を引き継がない。
- 変換後は派生正誤問題だけを出題し、元の集約回答問題は単独出題しない。
- 一次開発では派生問題内に元の問題文、元の解答指示、全記述を残す。
- 注釈、ハイライト、解答指示又は重複記述の非表示は一次開発の対象外とする。ハイライトは二次開発でも追加しない。
- 既存の問題報告機能を利用し、新しい報告機能を作らない。
- 実装は資格共通とし、第一種衛生管理者専用の分岐を作らない。同資格はtrialと公開scopeにだけ使用する。
- 既存の未コミット変更を破棄、巻き戻し、無関係にstageしない。
- pushは`origin/main`だけとし、force push又は別branchを使わない。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated. Do not stop after一部サンプルの成功。第一種衛生管理者の全件trial、公開前gate、限定公開readbackまで進める。

公開に必要な認証又は外部状態だけが残る場合は、その正確な状態と再実行手順をreceiptへ残し、その他の安全なローカル作業を完了する。

## Canonical Board

Machine truth lives at:

`docs/goals/aggregate-answer-statement-true-false/state.yaml`

## Run Command

```text
/goal Follow docs/goals/aggregate-answer-statement-true-false/goal.md.
```

