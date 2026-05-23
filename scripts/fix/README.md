# scripts/fix

patch 作業や既存 JSON の整備を補助するスクリプト置き場です。ここにあるスクリプトは「品質判断そのもの」を自動化するためではなく、退避、正式化、整合補完、検証前処理に使います。

## 通常運用で使う

- `archive_patch_outputs.py`
  - 既存 patch JSON を `old/` に退避する。
  - 01/02/03/04 prompt の再実施前に使う。
  - `question_intent` は `15_correctChoiceText_fixed/`、`correct_choice` は `23_correctChoiceText_fixed/` を退避する。
  - 複数資格で使う場合は、必ず `--base-dir output/<qualification>/questions_json` を指定する。
- `materialize_minimal_patch.py`
  - AI が作った最小 JSON を、正式 patch JSON に補完する。
  - `question_type`、`question_intent`、`correct_choice`、`explanation`、`question_set` 用。
- `auto_assign_correct_choice_text.py`
  - `answer_result_text` と `questionIntent` から merged JSON の `correctChoiceText` を下書き補完する。
  - `99.99%` レビューでは、この結果を必ず対象資格の専門家・問題作成者・参考書著者の観点で一問ずつ確認する。
- `rewrite_image_storage_urls.py`
  - 既存 JSON の画像 URL を Storage 公開 URL 形式へ正規化する。
- `backfill_answer_result_text_00_source.py`
  - `00_source` に `answer_result_text` が欠けている場合の補完に使う。
- `remove_answer_result_debug_fields.py`
  - Firestore upload 前に不要な debug field を除去する補助に使う。

## 注意して使う

- `backfill_answer_result_text_from_source_labels.py`
  - source labels から `answer_result_text` を補完する補助。
  - 実行前に対象資格・対象 `list_group_id` と入力根拠を確認する。

## legacy / 原則使わない

次のスクリプトは、現在の整理では `15_correctChoiceText_fixed/` と `23_correctChoiceText_fixed/` の責務を混同しやすい。日常運用では使わず、必要なら先に運用ドキュメントと実装責務を見直す。

- `add_answer_result_and_intent.py`
- `fill_question_intent_15_correctChoiceText_fixed_inplace.py`
- `fix_15_correctChoiceText_fixed_inplace.py`
- `migrate_correct_choice_patches_23_to_15.py`

## 判断基準

- `15_correctChoiceText_fixed/` は互換上の名前を維持しているが、現在の主用途は `questionIntent` patch。
- `23_correctChoiceText_fixed/` は最終 `correctChoiceText` の厳密レビュー対象。
- `correctChoiceText` の下書き補完は自動化してよいが、最終確定は `questionIntent`、`answer_result_text`、選択肢、元解説を専門家目線で一問ずつ突き合わせる。
- `explanationText` の本文はスクリプトで生成しない。
