# operations docs

このディレクトリは、`exam_scraper` の安定運用に使う正本ドキュメント置き場です。

## 正本

- [exam_pipeline_manual_and_automation.md](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md)
  - スクレイピング、patch、merge、convert、Storage / Firestore upload までの全体フロー。
  - フォルダ構成、主要コマンド、出力先、注意点を更新する場合はここを最初に直す。
- [../../tools/question_bank/README.md](/Users/yuki/development/exam_scraper/tools/question_bank/README.md)
  - 日常運用で直接叩く統一CLI。
  - 個別 script を増やす前に、ここから辿れるサブコマンドにできないか確認する。
- [goal_driven_update_workflow.md](/Users/yuki/development/exam_scraper/document/operations/goal_driven_update_workflow.md)
  - `qualification_code` / `list_group_id` / `question` 単位で goal に載せるための運用設計。
  - 日次更新や厳密レビューの粒度を変更する場合はここを直す。
- [lawzilla_mcp_practical_review_workflow.md](lawzilla_mcp_practical_review_workflow.md)
  - Lawzilla MCP を法令根拠整備の並列検証レイヤーとして使い、実務に耐えうるかのレビューを蓄積・定期フィードバックする運用。
  - API キーや endpoint を artifact / repo / 送付文面へ残さないための記録項目と送付前チェックもここで確認する。
- [ai_patch_execution_prompt_templates.md](/Users/yuki/development/exam_scraper/document/operations/ai_patch_execution_prompt_templates.md)
  - Codex / Gemini / Claude などへ patch 作成を依頼するときの省トークン指示テンプレート。
  - prompt の入力正本や出力先を変更した場合はここも直す。
- [google_drive_stream_migration.md](google_drive_stream_migration.md)
  - Google Drive stream 配下へ段階移行するときの検証手順とロールバック手順。
- [../reference/question_field_contract.md](/Users/yuki/development/exam_scraper/document/reference/question_field_contract.md)
  - 過去問データの共通フィールド契約。Firestore キー、型、DB 必須/整備必須、nullable、enum、資格固有ルールとの境界を確認する入口。
  - `questionType`、`correctChoiceText`、`lawReferences`、`suggestedQuestions` など、資格ごとに意味が揺れると困る field はここを正本として見る。
  - 毎回の機械チェックは `python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id>` を入口にする。

## goal テンプレート

- [manual-patch-quality](/Users/yuki/development/exam_scraper/docs/goals/templates/manual-patch-quality/README.md)
  - `correctChoiceText` を 99.99% 水準で専門家目線で精査し、`explanationText` も同時に品質確認するための汎用テンプレート。
  - 厳密運用では `1 Worker = 1問` を基本にする。

## 整理方針

- 旧メモや重複ドキュメントは、このディレクトリの正本へ統合する。
- `document/notes/` は調査メモ・履歴メモとして扱い、日常運用の正本にはしない。
- 新しい運用ルールを追加するときは、まず全体フロー、次に goal 運用、最後に prompt テンプレートの順で整合させる。
- prompt や scripts の責務を変えた場合は、関連する README も同時に更新する。
- 日常的に使う新しい検証・補助コマンドは、分散させず `tools/question_bank` から辿れるようにする。
- 共通 field の追加・削除・意味変更をする場合は、必ず `document/reference/question_field_contract.md` も更新する。
