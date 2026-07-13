# スクレイピングと`00_source`

この文書は、資格追加、問題取得、ID・画像生成、`00_source`保護の正本です。取得後のpatch作業は[prompt一覧](../../prompt/README.md)、保存先は[artifact契約](artifact_contract.md)を参照してください。

## 標準入口

資格ごとの差は`config/scrape_presets.json`に定義し、通常は次のランナーから実行します。

```bash
python3 scripts/scrape/run_qualification_scrape.py <qualification> <list_group_id>
```

主な実装:

- `scripts/scrape/run_qualification_scrape.py`: presetを解決してscraperを起動する。
- `scripts/scrape/common.py`: output、画像URL、canonical identityの共通処理。
- `code.py`: kakomonn系の互換入口。
- `scrape_<site>.py`: site固有parser。

ランナーがscraperへ渡す環境変数は`SCRAPER_QUALIFICATION_CODE`、`SCRAPER_QUALIFICATION_NAME`、`SCRAPER_LIST_FIRST_PAGE_URL`、`SCRAPER_OUTPUT_LIST_GROUP_ID`、任意の`SCRAPER_MAX_QUESTIONS`と`SCRAPER_OUTPUT_DIR`です。新しい設定値を増やす場合はpreset schema、runner、testsを同時に更新します。

## 新しい資格・サイトの追加

1. 対象年度・回、一覧URL、問題ページへの到達性を確認する。
2. [site台帳](../sources/README.md)を確認し、既存`scraper_type`で表現できる場合はpresetだけを追加する。
3. 新しいsite差分が必要な場合だけsite scraperを追加し、共通処理は`scripts/scrape/common.py`へ寄せる。
4. `config/scrape_presets.json`へ資格名、読みやすいローカル資格コード、年度・試験区分の出力ID、URL、対象範囲を登録する。
5. fixtureで本文、選択肢、正答、画像、IDを検証する。
6. 小さな取得で保存内容を確認してから、依頼された全年度・全公開groupを取得する。

site、実装、認証、既知制約の対応は[site台帳](../sources/README.md)を入口にします。site固有の抽出方針は`document/sources/<source>/`へ置き、共通ルールをsite文書へ複製しません。

## IDと出典

- canonical identityは資格、試験回、問番号、必要なsectionを基にし、site固有IDと分離する。
- `question_url`、`source_question_id`、`questionSourceSite`はprovenanceとして保持する。
- 既存Firestore IDがある更新では、その対応を維持する。
- URLや本文ハッシュだけを恒久IDにしない。
- ローカル資格コードと既存Firestoreの`qualificationId`が異なる場合は`publication_qualification_id`を明示し、公開IDを暗黙に変更しない。
- `source_list_group_id`は取得元siteのID、`output_list_group_id`は`YYYY`又は`YYYY01`・`YYYY02`の試験回IDとして分離する。

共通fieldの型と必須性は[question field契約](../reference/question_field_contract.md)が正本です。

## 画像

- ローカル画像は`output/<qualification>/question_images/<exam_occurrence_id>/`へ保存する。
- 公開先は`question_images/official/<qualification>/<filename>`のフラット構成とする。
- 同名画像が複数groupにある場合はhash一致を確認し、不一致なら停止する。
- JSONにはローカル一時パスではなく、変換契約に合う画像参照を残す。

## `00_source`不変条件

`00_source`はスクレイピング結果の正本です。

- 新規scrapeによる新規ファイル作成だけを許可する。
- 既存ファイルの内容とファイル名を手作業やfix scriptで変更・削除しない。
- 資格コード又は年度・試験区分を整理する親ディレクトリ移動だけは、file hashと`00_source/`以下の相対名を保持し、`--record-moves`でmanifestへ明示登録する。
- 修正は`10`、`15`、`18`、`21`、`22`、`23`、`24`のpatch層へ保存する。
- 新規取得後だけ次を実行してmanifestへ登録する。

```bash
python3 scripts/check/check_00_source_immutability.py --record-new
```

既存sourceの不整合を見つけても直接直さず、source conflict又はreview artifactへ記録して修正方針を決めます。

## 取得直後の確認

取得件数、ID重複、本文・選択肢、画像参照を確認し、source段階の必須fieldだけを検査します。

```bash
python3 tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id> \
  --mode required
```

patch coverageや`questionSetId`は後工程の責務なので、取得直後の不足と混同しません。
