# Report-origin correction overlay contract

challenge review が `fix` を通過した後、AI が直接既存 patch や `00_source` を編集してはいけません。`question_bank.py report-run` が次を決定論的に作ります。

- 保存先: `output/<qualification>/questions_json/<list_group_id>/24_questionIssueCorrections/`
- schema: `question-issue-correction/v1`
- origin: `user_problem_report`
- provenance: batch ID、case IDs、case input hashes、blind A/B hashes、challenge hash
- entry: `original_question_id`、`expectedBeforeHash`、カテゴリで許可された `changes`、客観的 rationale、公式・一次 evidence locator/hash

overlay は既存 01〜04 / 23 の後に適用します。`expectedBeforeHash` と現行 pre-overlay record hash が違えば merge は停止し、再レビューします。provenance と rationale は Firestore question document へ混ぜません。

公開順は correction checker → merge → prepare → quality-gate → upload dry-run → correction unit commit/push → upload → live readback です。`00_source` と Firestore の直接編集は禁止です。画像差し替えは新しいファイル名/URLを使い、旧画像は参照から外すだけで rollback 用に残します。
