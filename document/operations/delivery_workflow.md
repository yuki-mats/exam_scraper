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

## 機械品質ゲート

公開前の標準入口:

```bash
python3 tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

法令監査を必須にする資格では、CLI正本に記載されたlaw revision optionを追加します。既存の別資格・別groupの失敗と今回対象の失敗を分けて報告し、対象のgateを省略しません。

## 別セッション品質ゲート

機械品質ゲートを通った問題は評価待ちへ蓄積します。[問題整備システム](local_question_review_console.md)で、同じ作業回に限らず任意の整備済み問題を後から複数選択して評価できます。システムは選択した各元問題に新しい別セッションを割り当て、問題文と全選択肢を一体で読み、各選択肢の正誤、現在の正答対応、解説品質を判定します。

次のいずれかがあれば、その元問題を公開しません。

- 別セッション評価が未実施、実行中、失敗又は現在の内容より古い。
- 一肢以上が未確認、現在の正誤と不一致又は根拠不足。
- 公式正答、設問意図、`correctChoiceText`との不整合。
- 解説が90点未満又は重大指摘あり。
- 法令監査の`hold`又は公開前review state不足。
- merge、convert、upload dry-runの失敗又は古いartifact。

評価結果からシステムが問題ごとの`publishReady`を計算し、手動で合格に変更できないようにします。品質確認はpatchやupload-readyを直接変更しません。不合格だけを01、02、02a、02b、03、03bの該当工程へ戻し、再生成後に再評価します。同じ選択に含まれる他の問題の失敗は、合格した問題の公開可否を変えません。

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

## 問題整備システムからの公開

標準UXでは、問題詳細で最新評価が`publishReady=true`になった元問題だけ`この問題をFirestoreへ反映`を有効にします。元問題がFirestore上で複数documentへ分割されている場合は、その全documentを一つの公開単位として扱い、一部の選択肢だけを公開しません。

preflightはproject ID、元問題ID、Firestore document数、追加・更新件数、元artifact SHA、評価結果と問題内容のhashを固定します。削除、対象外document、評価の古さがあれば停止します。確認dialogの明示操作後だけ、元artifactから対象問題のdocumentを抽出した一時artifactを既存uploaderへ渡します。

実行直前にFirestore、ローカルhash、`publishReady`を再確認し、反映直後に同じdocumentを自動readbackします。upload成功だけでは完了にせず、全対象fieldが一致した場合だけ`Firestore反映済み`とします。preflight、対象artifact、result、readbackは`output/question_review_console/publish_runs/<qualification>/<runId>/`へ保存します。

## 公開境界

- Firestore schemaの最終正本はrepasoの`firestore.rules`とtyped model。exam_scraper側は`scripts/common/repaso_firestore_schema.py`で同期する。
- `00_source`のhashが作業前後で変わった場合は停止する。
- 既存`questionId`、`originalQuestionId`、作成日時を維持する。
- 差分のないdocumentは書き込まず、`updatedAt`を更新しない。
- 対象元問題の最新`publishReady=true`をserver側で再計算する。
- review artifactの公開flagをFirestore question documentへ追加しない。
- Firestore実反映はユーザー依頼又はUIの明示確認がある場合だけ行う。
- upload commandの成功だけで完了にせず、live readback一致を完了条件にする。
