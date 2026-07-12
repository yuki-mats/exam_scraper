# merge・検証・公開

この文書は、patchをupload-readyへ変換し、StorageとFirestoreへ安全に反映する工程の正本です。保存先は[artifact契約](artifact_contract.md)、検証optionは[question_bank CLI](../../tools/question_bank/README.md)を参照してください。

## upload-readyの生成

単一groupをmergeする場合:

```bash
python3 scripts/merge/00_merge_all.py <list_group_id> \
  --base-dir output/<qualification>/questions_json
```

通常はmerge、convert、upload dry-runまでをまとめます。

```bash
python3 scripts/pipeline/prepare_firestore_upload.py <list_group_id> \
  -b output/<qualification>/questions_json \
  --upload-dry-run
```

資格配下の全groupを更新する場合は`list_group_id`の代わりにqualificationを指定します。`--skip-merge`、`--skip-qset-check`、`--skip-update-category-counts`などは、前提を確認できる場合だけ使います。

## 品質ゲート

公開前の標準入口:

```bash
python3 tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

法令監査を必須にする資格では、CLI正本に記載されたlaw revision optionを追加します。既存の別資格・別groupの失敗と今回対象の失敗を分けて報告し、対象のgateを省略しません。

## 画像Storage

最初にdry-runします。

```bash
python3 scripts/upload/upload_question_images_to_storage.py \
  <qualification> --list-group-id <list_group_id> --dry-run
```

確認後に`--dry-run`を外します。既定では既存objectをskipし、`--overwrite`は明示的な差し替え時だけ使います。同名画像のhash衝突は停止条件です。

## category

```bash
python3 scripts/upload/upload_category_to_firestore.py \
  output/<qualification>/category/category.json \
  --licenseName "<資格名>"
```

上記はdry-run相当です。本番反映は差分と対象を確認した後に`--upload`を付けます。`questionSetId`は`category.json`の`questionSets[].questionSetId`を使い、`folderId`で代用しません。

## questions

```bash
python3 scripts/upload/upload_questions_to_firestore.py \
  output/<qualification>/questions_json/upload_to_firestore/<artifact>.json \
  --dry-run
```

本番反映は、同じartifactのSHA、project ID、追加・更新document数を確認してから`--dry-run`を外します。upload後は同じdocumentをreadbackし、対象fieldの一致を確認します。

## 公開境界

- Firestore schemaの最終正本はrepasoの`firestore.rules`とtyped model。exam_scraper側は`scripts/common/repaso_firestore_schema.py`で同期する。
- `00_source`のhashが作業前後で変わった場合は停止する。
- 既存`questionId`、`originalQuestionId`、作成日時を維持する。
- 差分のないdocumentは書き込まず、`updatedAt`を更新しない。
- Firestore実反映はユーザー依頼又はUIの明示確認がある場合だけ行う。
- upload commandの成功だけで完了にせず、live readback一致を完了条件にする。
