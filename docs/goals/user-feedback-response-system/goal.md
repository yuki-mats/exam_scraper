# ユーザーフィードバック対応システムを実装・常設する

## Objective

合意済みの目標仕様に従い、全資格の公式問題報告をMac稼働中に自動検知し、Codex App Serverによる独立AI審査、スマホでの一件承認、正式correction patchのcommit・push、既存公開フローへの合流までを行う常設システムを完成させます。

## Original Request

「実装コードも含めて変更をお願いします。古い実装の互換性は考えなくて良い。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 松田とRepaso運用
- Authority: `approved`
- Proof type: `demo`
- Completion proof: 専用clean cloneで常駐serviceが起動し、全資格・全報告が有効な状態でfixture全系統、スマホ承認、patch昇格、scoped commit/push、既存公開待ち、障害復旧、live-safe readbackが成功すること
- Goal oracle: `output/user_feedback_response_system/installation/result.json`と、Tailscale private HTTPSのスマホ実機walkthrough、test receipt、service/Git/Firestore/Codex App Server readbackの一致
- Likely misfire: UI又はfixtureだけを作って常設・全件有効化をしない、旧batch互換のため設計を複雑化する、又は承認時にFirestoreへ書き込む
- Blind spots considered: dirty worktreeとの衝突、旧status削除、Codex App Server停止、Mac sleep、Git競合、PII/prompt injection、5秒undo、訂正patch、全件一括有効化
- Existing plan facts: `document/operations/user_feedback_response_system.md`と`document/temporary/2026-07-19_user_feedback_response_system_implementation_plan.md`。ただし旧実装互換とmigrationは不要という最新指示を優先する

## Goal Oracle

The oracle for this goal is:

`専用clean cloneのmainで全testが通り、launchd常駐serviceとTailscale private HTTPS UIをスマホ実機から操作でき、全資格・全報告が自動審査scopeへ入り、承認済みfixtureが正式patchへ昇格してそのpatchだけをorigin/mainへpushし、承認操作自体はFirestore question writeを行わず、installation/result.jsonの全gateがpassになること。`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

既存計画と現行実装の差分を検証し、旧互換を持たない新しい公式問題レーンを最大安全単位で連続実装します。core intake/review/proposal、スマホ承認/patch/Git、常設/公開追跡/全件有効化を順に完成させ、最終auditまで止まりません。

## Non-Negotiable Constraints

- 初版は全資格の公式問題に対する全報告を一度に有効化する。
- 旧batch CLI、旧status、旧workflowとの後方互換、adapter、migrationは実装しない。新systemへ置き換える。
- 保存済み問題データ、既存ID、`00_source`不変条件は守る。
- 承認時は正式patchの検証・commit・`origin/main`へのpushまでとし、Firestore question writeは既存公開フローだけが行う。
- 専用clean clone`/Users/yuki/development/exam_scraper_feedback`の`main`だけで実装・常設し、別branch、force push、自動stashを使わない。
- Drive候補にある他作業の未コミット変更をstage、commit、rollbackしない。
- AIは既存のCodex App Serverだけを使い、外部providerへfallbackしない。
- スマホはTailscale private HTTPSだけで接続し、追加認証と通知を設けない。
- 報告者へ処理結果を返さず、reporter情報、raw report、思考過程をproposal、Git、通常ログ、archiveへ残さない。
- 実装変更は内容別に検証・commitし、`origin/main`へpushする。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated. Do not stop after one vertical slice while the full oracle still has safe local work. If production credentials or smartphone access block one slice, record the exact blocker and continue every safe local slice.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny. Each Worker should complete a coherent vertical slice with its tests and receipt.

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.0/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/user-feedback-response-system
```

## Canonical Board

Machine truth lives at:

`docs/goals/user-feedback-response-system/state.yaml`

## Run Command

```text
/goal Follow docs/goals/user-feedback-response-system/goal.md.
```

## PM Loop

1. Read this charter and GoalBuddy execution contract.
2. Read `state.yaml` and work only on the active task.
3. Keep one active task and one write worker.
4. Require bounded allowed files and verification for every Worker.
5. Save a receipt, update the board, and immediately activate the next safe slice.
6. Finish only when the final audit records `full_outcome_complete: true` against the oracle.
