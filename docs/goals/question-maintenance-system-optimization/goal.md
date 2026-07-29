# 問題整備システムの全体最適化

## Objective

既存の一問完結型・最大100問の連続補充・単一commit writerという安全な骨格を維持しながら、実測可能性、候補データ契約、評価の確定境界、公開操作を順番に改善する。各改善は、実装、局所検証、関連回帰検証、正本文書更新、commit、`origin/main`へのpushまで完了してから次へ進む。

## Original Request

「全体最適化になるように一つずつ改善して、一つずつ改善した結果が正しくなっているか検証しながら一つずつ修正してください。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 問題整備システムの運用者と、整備結果を利用する受験者
- Authority: `approved`
- Proof type: `test`, `metric`, `demo`
- Completion proof: 優先改善を一つずつ実装・検証・commit・pushし、最終回帰と代表runのreadbackで、安全性と改善効果を確認できること
- Goal oracle: 各改善のreceiptが対象不具合、変更ファイル、通過した検証、実測値、commitを対応付け、最終監査が全体目的への寄与を確認すること
- Likely misfire: 100並列を再実装する、古いrunの失敗へ個別条件を足す、計測せずに状態形式やプロセスを大規模分割する、又は複数改善を一括変更して原因と効果を判別不能にすること
- Blind spots considered: 実turnと予約futureの混同、旧runと現行版の混同、resume互換、評価失敗による有効結果の上書き、Firestore安全境界、UI操作待ちとpublisher処理時間の混同、状態書込が本当にボトルネックか未計測であること
- Existing plan facts:
  - 最初に実稼働並列度、queue wait、refill latency、writer lock waitを永続化する
  - 次に`valueJson`を廃止し、意味判断fieldだけを返す型付きcandidate DTOへ移行する
  - その後、未完成又はtool失敗の評価が最新の有効評価を上書きしないようにする
  - 続いて、対象を確認時点で固定する明示選択式の直列公開queueを整える
  - state v3又はUI/worker分離は、先行計測で必要性が示された場合だけ実施する
  - 同一threadでの補完評価、複数問model batch、Firestore並列writer、1問1patch、無条件の並列数増加は採用しない

## Goal Oracle

The oracle for this goal is:

`各改善が独立したcommitとしてorigin/mainへ反映され、対象テスト、関連回帰、00_source不変検査、成果物又はUI readbackが通り、最終監査で残存課題と不採用判断を実測値から説明できる。`

PMは各receiptをこのoracleへ対応付ける。一つのテスト成功、コード量の削減、画面表示だけの変更、又は旧runログだけでは完了としない。最終監査で`full_outcome_complete: true`を記録する。

## Goal Kind

`existing_plan`

## Current Tranche

第一改善として、問題整備runに実際のmodel turn並列度、queue wait、枠解放後の補充遅延、commit writerの待ち時間を記録する。予約future数ではなく実処理を測り、manifestとAPI/readbackから確認できるようにする。検証とcommit・pushが完了した後だけ、型付きcandidate DTOへ進む。

## Non-Negotiable Constraints

- `00_source`、既存question ID、既存patch、既存run成果物を移行書換えしない。
- 最大100問の連続補充、一問一turn、同一問題内の工程順、単一commit writerを維持する。
- 新規runの契約変更では、既存runをresume又はreadbackできる後方互換を保つ。
- モデルがserver-owned ID、hash、日時、確定状態を決める契約を増やさない。
- 機械検証は矛盾を検出して止める役割とし、一方の値へ自動補正しない。
- 一つの改善を検証、commit、`origin/main`へのpushまで閉じてから次の改善へ進む。
- 本目標の検証だけを理由に、本番Firestoreへ問題データを書き込まない。
- 仕様は既存の正本文書へ統合し、重複する工程一覧や別系統の運用文書を作らない。
- 古いrun由来の症状は、現行fixtureで再現した場合だけ修正対象にする。

## Stop Rule

Stop only when a final audit proves the full original owner outcome is complete.

安全な改善が残る限り継続する。ただし、先行計測が不要と示したstate v3、UI/worker分離、常駐uploaderは実施せず、その判断根拠をreceiptへ残す。

## Slice Sizing

一つのWorker packageは、利用者又は運用者が確認できる一つの改善を、実装・テスト・正本文書・readbackまで縦断して完了させる。複数の改善を同じdiffへ混ぜず、同じ改善を細かなhelperごとにも分割しない。

## Canonical Board

Machine truth lives at:

`docs/goals/question-maintenance-system-optimization/state.yaml`

## Run Command

```text
/goal Follow docs/goals/question-maintenance-system-optimization/goal.md.
```

## PM Loop

各継続時にこのcharter、GoalBuddy実行契約、`state.yaml`を読み、active taskだけを進める。Worker完了後は検証結果とdiffを確認し、その改善だけをcommit・pushしてreceiptへ記録する。次の改善は前の改善が閉じてから選ぶ。
