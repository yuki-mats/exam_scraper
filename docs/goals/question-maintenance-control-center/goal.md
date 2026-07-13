# 資格横断問題整備システム

## Objective

ローカル問題レビューUIを、新規スクレイピング資格を含む全資格について、01〜04・02b・03b、未作業、再実行、洗い替え、patch更新、merge、convert、quality-gate、upload-ready確認まで一貫して把握・実行できる問題整備システムへ拡張する。

## Original Request

これまで行ってきた問題整備作業を効率化するシステムとして、資格配下全体や未作業、新規洗い替えのworkflowをレビューUIへ統合し、現在の作業の流れと目的も確認できる最高のUXを実現する。

## Intake Summary

- Input shape: `specific`
- Audience: 問題整備を行う運用者
- Authority: `approved`
- Proof type: `demo`
- Completion proof: 新規スクレイピング資格と既存整備済み資格の双方で、資格単位のworkflow状態を表示し、必要な工程をUIから安全に進め、upload-readyまで到達できるブラウザ実演と自動テスト。
- Goal oracle: 実データを使ったデスクトップ・モバイルのブラウザwalkthrough、workflow APIテスト、最終成果物readback。
- Likely misfire: ガス主任技術者又は法令監査だけに最適化した画面や、進捗表示だけで実作業を進められないダッシュボードを作ること。
- Blind spots considered: 既存正本文書との重複、生成物からの状態推定、新規資格の空工程、本番Firestore誤操作、長時間jobの再開、資格横断で異なるfolder構成。
- Existing plan facts: 既存01〜04・02b・03b promptとoperations文書を正本とし、Firestore実アップロードは明示確認時だけ許可する。

## Goal Oracle

The oracle for this goal is:

`既存資格と新規スクレイピング相当fixtureの双方で、UIがworkflow全体・目的・未作業理由・次アクションを正しく示し、対象抽出からupload-ready検証までを安全に実行できることを、ブラウザwalkthroughと自動テストで証明する。`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`specific`

## Current Tranche

現行workflow・artifact・UI/APIを調査し、共通状態モデルと安全な実行契約を設計したうえで、資格単位のoverview、工程別状態・目的・次アクション、未作業／再実行／洗い替え導線、長時間job、検証結果を実装し、実データと新規資格fixtureでend-to-end検証する。安全なローカル作業が残る限り継続する。

## Non-Negotiable Constraints

- `AGENTS.md`、既存prompt、operations文書、field contractを先に確認し、既存の正本を個別UIへ複製しない。
- ガス主任技術者、単一年度、法令監査だけに限定しない。
- `00_source`の既存ファイルは変更・削除・改名しない。
- 既存の`questionId`、`originalQuestionId`、`questionSetId`を不用意に変更しない。
- 対象外の未コミット変更を破棄しない。
- 本番Firestoreへの実アップロードはユーザーの明示確認がある場合だけ実行する。
- UIは目的・現在地・次の作業を短時間で把握でき、既存の単問レビューを損なわない。
- 実装後は自動テスト、実成果物、デスクトップ・モバイルのブラウザ確認を行う。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or a single partial dashboard slice while safe implementation work remains. Production Firestore approval may block only the production-write slice; all local and dry-run work must continue.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny. Prefer complete vertical slices such as workflow inventory plus API plus usable UI plus browser verification.

## Canonical Board

Machine truth lives at:

`docs/goals/question-maintenance-control-center/state.yaml`

## Run Command

```text
/goal Follow docs/goals/question-maintenance-control-center/goal.md.
```

## PM Loop

On every `/goal` continuation, read this charter, the GoalBuddy execution contract, and `state.yaml`; work only on the active task; record a receipt; advance to the next largest safe slice; and finish only after a final audit records `full_outcome_complete: true`.
