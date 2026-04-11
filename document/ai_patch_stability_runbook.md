# AIパッチ作業 Runbook

旧AI patch 自動実行スクリプトは廃止した。現在の標準運用は、各 task の patch を手動または Codex/Gemini/Claude で作成し、`00_merge_all.py` と `prepare_firestore_upload.py` で下流を更新する流れである。

## 1. 標準入口

- patch ルール: `prompt/01_prompt_fix_questionType.md` から `prompt/04_prompt_link_questionSetId.md`
- prompt 対応表: `skills/exam-firestore-pipeline/references/prompt-map.md`
- merge: `scripts/merge/00_merge_all.py`
- Firestore 前処理: `scripts/pipeline/prepare_firestore_upload.py`

## 2. 基本フロー

1. `00_source` を確認して対象 `list_group_id` を決める
2. task ごとに patch JSON を作成する
3. task ごとの check を通す
4. `00_merge_all.py` で `20_merged_1` / `30_merged_2` を更新する
5. `prepare_firestore_upload.py` で `40_convert` と `upload_to_firestore` を更新する
6. 必要なら category / questions を Firestore に upload する

## 3. task ごとの保存先

- `questionType`: `output/<qualification>/questions_json/<list_group_id>/10_questionType_fixed/`
- `explanationText`: `output/<qualification>/questions_json/<list_group_id>/21_explanationText_added/`
- `questionSetId`: `output/<qualification>/questions_json/<list_group_id>/22_questionSetId_linked/`
- `correctChoiceText`: `output/<qualification>/questions_json/<list_group_id>/23_correctChoiceText_fixed/`

旧 patch は `old/` に退避し、各 source に対して最新 patch だけを正本として扱う。

## 4. よく使うコマンド

```bash
cd /Users/yuki/development/exam_scraper

# 例: merge
python3 scripts/merge/00_merge_all.py 85010 \
  --base-dir output/2nd-class-kenchikushi/questions_json

# 例: 単一 list_group_id の Firestore 前処理
python3 scripts/pipeline/prepare_firestore_upload.py 85010 \
  -b output/2nd-class-kenchikushi/questions_json \
  --questionset-only

# 例: 資格単位の Firestore 前処理
python3 scripts/pipeline/prepare_firestore_upload.py 2nd-class-kenchikushi \
  --questionset-only
```

## 5. 確認ポイント

- `questionSetId` は `category.json` の `questionSets[].questionSetId` に存在すること
- `40_convert` と `upload_to_firestore` は各 `list_group_id` の最新 1 本を使うこと
- `category.json` の件数更新は `2_update_category_counts.py --latest-final-only --write` を使うこと
- questions upload は `--dry-run` で内容確認してから本番実行すること
