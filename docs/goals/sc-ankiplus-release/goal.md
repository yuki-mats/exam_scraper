# 情報処理安全確保支援士の過去問整備と暗記プラス公開

## 目的と依頼

支援士の過去問を正確に取得・整備し、暗記プラスのアプリとして公開することを最終目標とする。取得元はユーザー指定の https://www.sc-siken.com/sckakomon.php 。取得・全件精査は完了した。問題ごとの整備・独立評価とアプリ公開は未完了である。

## 完了を判定する証拠

公開回・問題URL集合の全件一致、IPA公式正答の全件一致、問題ごとの現行整備・独立評価receipt、Firestore反映後のreadback、暗記プラスの実機操作証拠、App Storeの公開状態の現行readbackをそろえる。`state.yaml`を進捗・task・receiptの正本とする。

## 制約

AGENTS.mdと問題整備workflowに従い、mainのみを使う。既存IDと00_sourceを保護し、修正は責務に合うpatchへ保存する。午前選択式と午後記述式の学習方式を混同しない。取得元の独自解説をそのまま公開せず、日本語品質の正本に沿って整備する。本番データ反映とiOS提出の確認を分ける。今回は定期実行を設定しない。

## 現在の段階

T001の取得・全件精査は完了し、receiptを保存した。T002は次のrunの最初のread-only taskとして選定しているが、Agentはまだ実行していない。続く工程は実行準備であり、自動開始済みとは扱わない。未確定の公開scopeやrelease設定はread-only調査結果を基に確認する。取得完了だけでは全体目標を完了にしない。

## 実行手順

Codex: `/goal Follow docs/goals/sc-ankiplus-release/goal.md.`

Claude Code: `/goalbuddy Follow docs/goals/sc-ankiplus-release/goal.md.`

実行開始時はGoalBuddyの`references/goal-execution.md`を読み、active taskだけを進める。各段階の証拠を保存してtask receiptを更新する。仕様はこの実行記録へ複製せず、責務に合う既存正本へ保存する。
