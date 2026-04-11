# スクレイピング → Firestore アップロードの現行フロー

現在の運用は旧自動実行スクリプトを使わず、patch を task 単位で作成してから `prepare_firestore_upload.py` で最終 JSON を更新する。

## 1. 出力ディレクトリ

```text
output/<qualification>/questions_json/<list_group_id>/
  00_source/
  10_questionType_fixed/
  20_merged_1/
  21_explanationText_added/
  22_questionSetId_linked/
  23_correctChoiceText_fixed/
  30_merged_2/
  40_convert/

output/<qualification>/questions_json/
  upload_to_firestore/

output/<qualification>/category/
  category.json
```

## 2. 基本フロー

1. スクレイピング
   - `python3 code.py <list_group_id>`
2. patch 作成
   - `prompt/01` から `prompt/04` と `skills/exam-firestore-pipeline/references/prompt-map.md` を使う
3. merge
   - `python3 scripts/merge/00_merge_all.py <list_group_id> --base-dir output/<qualification>/questions_json`
4. Firestore 前処理
   - 単一: `python3 scripts/pipeline/prepare_firestore_upload.py <list_group_id> -b output/<qualification>/questions_json --questionset-only`
   - 資格一括: `python3 scripts/pipeline/prepare_firestore_upload.py <qualification> --questionset-only`
5. Firestore upload
   - category: `scripts/upload/upload_category_to_firestore.py`
   - questions: `scripts/upload/upload_questions_to_firestore.py`

## 3. よく使うファイル

- `prompt/01_prompt_fix_questionType.md`
- `prompt/02_prompt_fix_correctChoiceText.md`
- `prompt/03_prompt_add_explanationText.md`
- `prompt/04_prompt_link_questionSetId.md`
- `skills/exam-firestore-pipeline/references/prompt-map.md`
- `scripts/merge/00_merge_all.py`
- `scripts/pipeline/prepare_firestore_upload.py`
- `document/ai_patch_stability_runbook.md`

## 4. チェックポイント

- `00_source` と merged の問題数に大きな欠落がないか
- `questionSetId` が `category.json` の `questionSets[].questionSetId` に存在するか
- `upload_to_firestore` の最新 JSON が対象 `list_group_id` と一致しているか
- `category.json` の件数更新は `--latest-final-only` 付きで行うこと
