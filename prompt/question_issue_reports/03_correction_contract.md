# Report-origin correction overlay contract

challenge review が `fix` を通過した後、AI が直接既存 patch や `00_source` を編集してはいけません。`question_bank.py report-run` が次を決定論的に作ります。

- 保存先: `output/<qualification>/questions_json/<list_group_id>/24_questionIssueCorrections/`
- schema: `question-issue-correction/v1`
- origin: `user_problem_report`
- provenance: batch ID、case IDs、case input hashes、blind A/B hashes、challenge hash
- entry: source由来の`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`、`original_question_id`、`expectedBeforeHash`、カテゴリで許可された`changes`、客観的rationale、公式・一次evidence locator/hash

overlay は既存 01〜04 / 23 の後に適用します。3要素のsource identityを完全一致させ、旧entryはsource内で一意な場合だけ適用します。曖昧、未対応又は`expectedBeforeHash`と現行pre-overlay record hashが異なる場合はmergeを停止し、再レビューします。provenanceとrationaleはFirestore question documentへ混ぜません。

公開順は correction checker → merge → prepare → quality-gate → upload dry-run → correction unit commit/push → upload → live readback です。`00_source` と Firestore の直接編集は禁止です。画像差し替えは新しいファイル名/URLを使い、旧画像は参照から外すだけで rollback 用に残します。
