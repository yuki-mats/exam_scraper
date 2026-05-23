# goals

Codex / GoalBuddy で長めの運用タスクを回すための goal 定義置き場です。

## ディレクトリ

- `templates/`
  - 新しい goal を作るための汎用テンプレート。
  - 厳密な一問レビューは `templates/manual-patch-quality/` を使う。
- `<slug>/`
  - 実行中または完了済みの個別 goal。
  - `goal.md` と `state.yaml` を1セットで管理する。

## 運用ルール

- 新しい品質改善 goal は、テンプレートをコピーして slug を切る。
- `state.yaml` では、厳密レビュー対象を `1 Worker = 1問` に分解する。
- 取得、品質改善、publish は同じ goal に詰め込みすぎない。
- 全体フローの正本は [document/operations/exam_pipeline_manual_and_automation.md](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md) を参照する。
