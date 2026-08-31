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

### ガス主任技術者の全年度再生成

ガス主任技術者の2017〜2025年は、公式PDFで検証済みの`25_verified_publication`をローカル公開正本とし、そこから`30_merged_2`と`40_convert`を一括再生成します。通常実行では旧成果物、Firestore snapshot、live readbackを入力にしません。最初に書込みなしで検査します。

```bash
python3 scripts/pipeline/rebuild_gas_shunin_all_year_artifacts.py \
  --receipt /tmp/gas-shunin-all-year-rebuild.json
```

receiptの甲種2,212問・乙種1,913問がともに`status=pass`であることを確認した後、`--apply`を付けます。既存の生成物は各`old/`へ退避し、`25_verified_publication`から18年度分を再生成します。`00_source`とFirestoreは変更しません。

`--bootstrap-from-snapshots`は移行時だけの入口です。公式PDF台帳が全対象IDを一意に裏付け、Firestore snapshotの件数とIDが期待値へ完全一致する場合に限り、snapshotを`25_verified_publication`へ固定します。移行後の再生成ではこのoptionを使いません。Firestoreへ差分を反映する場合は、全年度artifactを一括上書きせず、許可fieldを明示した限定patch、事前fingerprint、rollback、反映後readbackを使います。

ガス主任技術者の標準quality gateは、旧工程のpatch有無ではなく、公式PDFの現物hash、`25_verified_publication`、`category.json`、再生成済み`30_merged_2`・`40_convert`の完全一致を検査します。

## 機械品質ゲート

公開前の標準入口:

```bash
python3 tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

法令監査を必須にする資格では、CLI正本に記載されたlaw revision optionを追加します。既存の別資格・別groupの失敗と今回対象の失敗を分けて報告し、対象のgateを省略しません。

patchの機械検査は`00_source`単体の旧fieldではなく、先行工程の確定patchを順に適用した現在のprojectionを判定対象にします。問題recordは`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`の組で照合し、並列処理の完了順になったpatch配列へ`00_source`と同じ配列順を要求しません。特に解説の件数・文体は現行`questionType`、設問意図及び正答で検査します。非法令問題に残す`not_law_related/secondary_verified`の内部監査メモへFirestore公開objectのschemaを直接適用せず、公開schemaはmerge・convert後の成果物で検査します。これにより、現在の公開内容へ影響しない旧表現を保留として扱わず、実質的な不整合だけを停止します。

問題整備システムは、年度の現在projectionとmerge・convert・upload-readyに差分がある場合、年度一覧へ`公開用データを再生成`を表示します。過去runの成功表示ではなく現在の成果物差分を判定し、この操作から対象年度だけをmerge、convert、upload dry-runまで更新します。

## 別セッション品質ゲート

機械品質ゲートを通った問題は評価待ちへ送ります。整備・評価・再整備のsession分離、評価方法、サブスクリプション境界は[問題整備システム](local_question_review_console.md)だけを正本とします。

次のいずれかがあれば、その元問題を公開しません。

- 適用対象の整備工程に現行MAJOR未満又は未記録がある。
- 現在の問題内容に対する別session評価へ合格していない。
- 合格した評価の評価MAJORが現行でない。
- 全選択肢の根拠、正答対応、解説品質又は法令監査に未解決事項がある。
- merge、convert、upload dry-runのいずれかが失敗又は現在内容より古い。

`publishReady`はserverだけが計算し、手動変更を受け付けません。不合格は新しい再整備sessionへ送り、成果物を再生成した後、さらに新しい評価sessionで確認します。

## 画像Storage

独自問題では、`00_source`に問題画像又は選択肢画像がある場合、05で問題文・設問・選択肢・正答を確定してから、その内容に合う画像を作ります。画像なしの中間projectionは確認できますが、独自生成画像が揃うまでartifact同期、upload-ready生成、Firestore uploadを停止します。取得元画像の再利用を許可するのは、取得元全体を公式過去問と確認して通常工程へ進めた問題だけです。判定とファイル名の詳細は[独自問題作成ワークフロー](original_question_authoring_workflow.md#画像の扱い)を正本とします。

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

`questionCount=0`のfolderとquestionSetは`isDeleted=true`として非表示にします。既存の0問項目だけを限定反映する場合は`--hide-empty-only`を使い、問題、件数、名称、所属先を変更しません。

## questions

```bash
python3 scripts/upload/upload_questions_to_firestore.py \
  output/<qualification>/questions_json/upload_to_firestore/<artifact>.json \
  --dry-run
```

本番反映は、同じartifactのSHA、project ID、追加・更新document数を確認してから`--dry-run`を外します。upload後は同じdocumentをreadbackし、対象fieldの一致を確認します。

## 問題整備システムからの公開

標準UXでは、`公開可能`で絞り込んだ一覧から1〜100問を明示選択します。問題詳細の`この問題をFirestoreへ反映`も同じ公開queueへ一問だけ渡します。previewは指定順と各問題のpreflight tokenを一つの親tokenへ固定し、問題名、元問題ID、document数、追加・更新件数をすべて表示します。一問でも公開不可又は差分なしなら対象を黙って除外せず、全問題を書き込み前に停止します。

確認dialogの明示操作後、serverは同じ問題集合を再previewし、単一のrepository排他jobで指定順に一問ずつ処理します。各問のpreflightはproject ID、元問題ID、Firestore document数、追加・更新件数、元artifact SHA、`00_source` hash、確認時のFirestore値、問題内容のhash、適用工程の作業版、評価版を固定します。candidate又は既存Firestoreの`isDeleted=true`、既存documentの資格・年度・元問題ID不一致、対象外document、現行MAJOR未満・未記録、評価の古さがあれば、その問は書き込みません。

各問は実行直前にFirestore、ローカルhash、`publishReady`を再確認し、uploader内でも確認時のFirestore値とdocument更新時刻を照合して同時更新の上書きを拒否します。反映直後に同じdocumentを自動readbackし、全対象fieldの一致と`00_source`不変を確認できた問だけ成功とします。一問の失敗は問題単位のfailed receiptへ残して次問へ進み、自動再試行しません。問題単位のpreflight、対象artifact、result、readbackは`publish_runs/`、選択順とqueue全体の終端集計は`publish_queue_runs/`へ分けて保存します。

## 公開境界

- Firestore schemaの最終正本はrepasoの`firestore.rules`とtyped model。exam_scraper側は`scripts/common/repaso_firestore_schema.py`で同期する。
- `00_source`のhashが作業前後で変わった場合は停止する。
- 既存`questionId`、`originalQuestionId`、作成日時を維持する。
- 差分のないdocumentは書き込まず、`updatedAt`を更新しない。
- 対象元問題の最新`publishReady=true`をserver側で再計算する。
- 適用対象の全整備工程と評価が現行MAJORであることをserver側で再確認する。
- review artifactの公開flagをFirestore question documentへ追加しない。
- Firestore実反映はユーザー依頼又はUIの明示確認がある場合だけ行う。
- upload commandの成功だけで完了にせず、live readback一致を完了条件にする。
