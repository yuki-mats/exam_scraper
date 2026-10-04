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

公式問題の`examSource`に使う資格名は、明示的な`--exam-name`を最優先し、通常は入力pathのローカル資格コードから`config/scrape_presets.json`の`qualification_name`を解決します。GUIも同じ資格配下のpathをconvertへ渡します。UI専用の表示名catalogや公開用`qualificationId`を資格名へ置き換えません。presetがない旧入力では従来の資格・group対応と問題内の試験名推定を使い、同じローカル資格コードのpreset情報が競合する場合は変換を停止します。

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

限定artifactの検証では、正式入力からの生成と未承認4field候補からの生成を各2回実行し、同入力のprojection・全document・内容hash・公開ID集合を照合します。既存strict law coverageはmerged、converted、upload-readyの各明示入力へ適用し、正式現状の失敗をprivate候補の成功で置き換えません。正式入力のPublisher loaderと候補の拒否、両経路の評価・公開preview、subscription判定を個別に保存します。正しい拒否は接続検証の結果であり、評価合格や公開準備完了の代替にはなりません。
# 限定 field 修正の承認境界

`prepare_scoped_question_artifacts.py --snapshot-corrections ... --preview-only` は private preview を作成します。
正式patch保存承認と production 適用承認は別gateです。native内容審査は機械checkpointやmodel evaluationではありません。
2025追加4fieldの承認pendingはこの経路で昇格しません。固定法令基準日は2026-10-03で、正式保存前に現行版再照合が必要です。

source/candidate/policy/work version/manifest/live snapshot の hash を token に結合し、真正工程と評価を確認します。
未承認・未実行なら blocker を返します。更新gatewayは全対象docのbeforeとupdateTimeを確認した後、
全precondition付き atomic batch/transaction で field update を実行します。set/merge/full upload/deleteは使いません。
監査フィールドの既存責務は内容deltaと別に承認します。適用後は同じmaskでafter/presenceを確認します。
再送には同じ操作receiptとafter versionが必要です。rollbackは別承認の補償契約で、競合時や削除を要する場合は拒否します。
T040実行はpreview/read-onlyとfixture検証のみで、本番write・正式保存・evaluationを実行しません。


## 限定正式保存のdry-run

`prepare_scoped_question_artifacts.py --formal-save-plan <固定manifest> --output <private run> --dry-run` は、正式予定9fileと同一bytesをprivate `planned-files/`へ生成する。実正式保存は行わない。2018/2024は原問本文・肢順・原選択答4/2を保持するsource no-op envelopeと公開TF variantを分離する。2020はproduction snapshot専用型でsource group/ref=null、questionText一文字deltaだけを保持する。local評価namespaceを正式sourceに代用しない。

`formal-save-plan.json`は全path/file SHA256・固定候補・native証跡・現入力hashを結合する。object hashはcompact sorted UTF-8 JSON（LFなし）、file hashは実pretty JSON bytes（末尾LFあり）。歴史的コード依存との差は新planに記録し、旧検証を現行コード成功へ流用しない。

正式保存APIは、固定質問のexact plan/path/hash bindingに関連する真正native人間回答と、別の日時・URL・revision・XML/body hash付き現行法取得証跡を要求する。bool/tokenや人間の法令真偽宣言だけでは許可しない。UI回答はquestionItemIdのtool/call/index・固定質問を照合する。法令差異は自動補正しない。保存はunitごとのOS atomic no-replace確定で、同一bytesの再送だけを受理する。receipt未確定のdirectoryは有効なunitとして読まない。

`load_formal_correction_unit`、Inventory、ArtifactSynchronizerの明示入口だけでplan/承認receiptを読む。通常24/05 selector・group resolverは不変。保存receiptからcheckpoint/evaluation/machineReady/publicationReadyを作らない。正式保存・現在法再照合・対象checkpoint/model評価・本番承認・SDK更新・公開後readbackは別工程である。


### 結合済みreviewの凍結と限定保存の回復

planに結合したcase_review本文は、行政的な承認待ち表示・進捗同期では変更しない。PMはplan hash・review hash・状態・receipt参照を別のappend-only linkageへ記録し、そのlinkageを同planのinputsに加えない。判断・根拠・候補の実質変更では旧planを失効させfresh planを生成する。2025の既存回答待ちは再送しない。

承認Markdownと詳細refs/facts付録の実bytes hashを、循環参照なしでplanと固定質問に結合する。法令gateはdated law_data実metadata応答内revisionと、XML実bytes/MainProvision/body hashを照合する。保存中のapproved bytes・staged bytes・現inputsをexclusive finalize直前に再照合する。成功receiptの同一再送は元法令証跡を確認するread-only操作であり、日付変更後も元receiptを返す。receiptなし中断resumeは新write/receipt確定を伴うためfresh法令再照合が必要。
