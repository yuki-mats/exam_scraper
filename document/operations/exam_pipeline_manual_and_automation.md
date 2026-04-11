# `code.py` から Firestore upload までの全体フロー

このメモは、`code.py` のスクレイピングから Firestore upload までの現行運用をまとめたもの。旧AI patch 自動実行スクリプトは廃止し、現在は task ごとの patch 作成と `prepare_firestore_upload.py` を組み合わせて運用する。

## 正本の場所

- スクレイピング実装:
  `/Users/yuki/development/exam_scraper/code.py`
- Firestore 前処理:
  `/Users/yuki/development/exam_scraper/scripts/pipeline/prepare_firestore_upload.py`
- merge:
  `/Users/yuki/development/exam_scraper/scripts/merge/00_merge_all.py`
- upload:
  `/Users/yuki/development/exam_scraper/scripts/upload/upload_questions_to_firestore.py`
  `/Users/yuki/development/exam_scraper/scripts/upload/upload_category_to_firestore.py`
- prompt:
  `/Users/yuki/development/exam_scraper/prompt/`
- skill:
  `/Users/yuki/development/exam_scraper/skills/exam-firestore-pipeline/`

## 1. `code.py` で最初に設定するもの

- `LIST_FIRST_PAGE_URL`
- `QUALIFICATION_CODE`
- `QUALIFICATION_NAME`
- `JSON_SUBDIR_NAME`
- `MAX_QUESTIONS`
- `TARGET_URL`
- `TARGET_LIST_PAGE_NUMBER`
- `UPDATE_JSON_MODE`

`python3 code.py 85010` のように `list_group_id` を CLI 引数で上書きできる。

## 2. `code.py` が自動で行うこと

- 一覧ページから問題 URL を収集する
- 各問題ページから問題文、選択肢、解説、画像 URL を抽出する
- `public_question_id` を生成する
- 画像ファイルをローカル保存する
- `00_source` JSON を出力する

## 3. 出力先

```text
output/<qualification>/
  question_images/<list_group_id>/
  questions_json/<list_group_id>/
    00_source/
    10_questionType_fixed/
    20_merged_1/
    21_explanationText_added/
    22_questionSetId_linked/
    23_correctChoiceText_fixed/
    30_merged_2/
    40_convert/
  questions_json/upload_to_firestore/
  category/category.json
```

画像の扱い:

- ローカル画像は引き続き `output/<qualification>/question_images/<list_group_id>/` に保存する。
- Firestore JSON に入れる公開URLは `question_images/official/<qualification>/<filename>` のフラット構成を正本とする。
- `list_group_id` や年度は公開URLに含めない。
- Storage への画像アップロードも同じフラット構成にする。

## 4. スクレイピング

```bash
cd /Users/yuki/development/exam_scraper
python3 code.py 85010
```

## 5. AI 整形

AI 整形は task ごとの patch を手動または Codex/Gemini/Claude で作る。対応する prompt と保存先は `skills/exam-firestore-pipeline/references/prompt-map.md` を正本とする。

主な task:

- `question_type`
- `correct_choice`
- `question_set`
- `explanation`

patch 作成後は、必要な check を通したうえで merge に進む。

## 6. merge

```bash
python3 scripts/merge/00_merge_all.py 85010 \
  --base-dir output/2nd-class-kenchikushi/questions_json
```

## 7. Firestore 前処理

単一 `list_group_id` を処理する場合:

```bash
python3 scripts/pipeline/prepare_firestore_upload.py 85010 \
  -b output/2nd-class-kenchikushi/questions_json \
  --questionset-only
```

資格コードを指定して配下の全 `list_group_id` を順に更新する場合:

```bash
python3 scripts/pipeline/prepare_firestore_upload.py 2nd-class-kenchikushi \
  --questionset-only
```

必要に応じて使うオプション:

- `--skip-merge`
- `--skip-qset-check`
- `--skip-update-category-counts`
- `--upload-dry-run`
- `--dry-run`

既存 JSON の画像URLを一括で正規化したい場合:

```bash
python3 scripts/fix/rewrite_image_storage_urls.py \
  --output-root output
```

確認だけ行う場合:

```bash
python3 scripts/fix/rewrite_image_storage_urls.py \
  --output-root output \
  --dry-run
```

このコマンドは `questions_json/` 配下の現行 JSON だけを対象にし、各ディレクトリの `old/<timestamp>/` に退避してから書き換える。既存の `old/` 配下は対象外。

## 8. 画像 Storage upload

資格配下の画像を一括で確認する場合:

```bash
python3 -m pip install -r requirements_firestore.txt

python3 scripts/upload/upload_question_images_to_storage.py \
  2nd-class-kenchikushi \
  --dry-run
```

未アップロード画像だけをアップロードする場合:

```bash
python3 scripts/upload/upload_question_images_to_storage.py \
  2nd-class-kenchikushi
```

既存画像も再アップロードする場合:

```bash
python3 scripts/upload/upload_question_images_to_storage.py \
  2nd-class-kenchikushi \
  --overwrite
```

特定の `list_group_id` だけ確認する場合:

```bash
python3 scripts/upload/upload_question_images_to_storage.py \
  2nd-class-kenchikushi \
  --list-group-id 85010 \
  --dry-run
```

このコマンドはローカルの `question_images/<list_group_id>/` を走査し、Storage へは `question_images/official/<qualification>/<filename>` としてアップロードする。同名画像が複数の `list_group_id` にある場合はハッシュ一致を確認し、一致すれば1回だけアップロードする。不一致なら衝突として停止する。

デフォルト対象は `questions_json/<list_group_id>/` が存在する `list_group_id` の画像だけに限定する。`.DS_Store` などの隠しファイルと非画像拡張子はアップロード対象外。

## 9. Firestore upload

category dry-run 相当:

```bash
python3 scripts/upload/upload_category_to_firestore.py \
  output/2nd-class-kenchikushi/category/category.json \
  --licenseName "二級建築士"
```

category 本番:

```bash
python3 scripts/upload/upload_category_to_firestore.py \
  output/2nd-class-kenchikushi/category/category.json \
  --licenseName "二級建築士" \
  --upload
```

questions dry-run:

```bash
python3 scripts/upload/upload_questions_to_firestore.py \
  output/2nd-class-kenchikushi/questions_json/upload_to_firestore/85010_firestore_<YYYYMMDD_HHMMSS>.json \
  --dry-run
```

questions 本番:

```bash
python3 scripts/upload/upload_questions_to_firestore.py \
  output/2nd-class-kenchikushi/questions_json/upload_to_firestore/85010_firestore_<YYYYMMDD_HHMMSS>.json
```

## 10. 注意点

- `questionSetId` は `category.json` の `questionSets[].questionSetId` を正本とする
- `folderId` を `questionSetId` に使わない
- `upload_questions_to_firestore.py` は `--dry-run` を外すと本番 upload になる
- `upload_category_to_firestore.py` は `--upload` を付けた時だけ本番 upload になる
- `upload_question_images_to_storage.py` は既定で既存 object をスキップし、`--overwrite` の時だけ再アップロードする
- `category.json` の件数更新は `2_update_category_counts.py --latest-final-only --write` を使う
