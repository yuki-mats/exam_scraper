# operations docs

このディレクトリは、`exam_scraper` の安定運用に使う正本ドキュメント置き場です。

## 正本

- [exam_pipeline_manual_and_automation.md](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md)
  - スクレイピング、patch、merge、convert、Storage / Firestore upload までの全体フロー。
  - フォルダ構成、主要コマンド、出力先、注意点を更新する場合はここを最初に直す。
- [goal_driven_update_workflow.md](/Users/yuki/development/exam_scraper/document/operations/goal_driven_update_workflow.md)
  - `qualification_code` / `list_group_id` / `question` 単位で goal に載せるための運用設計。
  - 日次更新や厳密レビューの粒度を変更する場合はここを直す。
- [ai_patch_execution_prompt_templates.md](/Users/yuki/development/exam_scraper/document/operations/ai_patch_execution_prompt_templates.md)
  - Codex / Gemini / Claude などへ patch 作成を依頼するときの省トークン指示テンプレート。
  - prompt の入力正本や出力先を変更した場合はここも直す。

## goal テンプレート

- [manual-patch-quality](/Users/yuki/development/exam_scraper/docs/goals/templates/manual-patch-quality/README.md)
  - `correctChoiceText` を 99.99% 水準で専門家目線で精査し、`explanationText` も同時に品質確認するための汎用テンプレート。
  - 厳密運用では `1 Worker = 1問` を基本にする。

## 整理方針

- 旧メモや重複ドキュメントは、このディレクトリの正本へ統合する。
- `document/notes/` は調査メモ・履歴メモとして扱い、日常運用の正本にはしない。
- 新しい運用ルールを追加するときは、まず全体フロー、次に goal 運用、最後に prompt テンプレートの順で整合させる。
- prompt や scripts の責務を変えた場合は、関連する README も同時に更新する。
