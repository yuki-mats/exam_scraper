# T001 現行経路と未整合の調査

## 現行から目標への経路

1. 取得設定は`config/scrape_presets.json`、入口は`scripts/scrape/run_qualification_scrape.py`、site差分はsite scraper、共通ID・画像処理は`scripts/scrape/common.py`が所有する。
2. 取得結果は`output/<qualification>/questions_json/<listGroupId>/00_source/`へ保存する。`source_question_id`はsite不変ID、`question_url`と`questionSourceSite`はprovenance、独自問題の`listGroupId`は講座・問題集の安定名を使う。
3. 目標契約では`05_originalized`を01より前のpatchとして適用し、その後は既存の01〜04、Merge、Convert、uploadを再利用する。
4. 公開する独自問題は`isOfficial=true`、`examSource="独自問題"`、`examYear` omitとし、取得元の原文・URL・`source_question_id`は公開artifactへ入れない。

## 実装証拠

- `document/operations/scraping_workflow.md`と`document/sources/README.md`はpreset駆動、site scraper、`00_source`不変条件を正本化している。
- `document/operations/original_question_authoring_workflow.md`、`document/operations/artifact_contract.md`、`document/reference/question_field_contract.md`は05と独自問題の公開条件を既に目標形で説明している。
- `scripts/merge/00_merge_all.py`は10、15、18、21、22、23、24だけを読み、05を読み込まない。
- `config/question_maintenance_workflow.toml`に05 stageがない。
- `config/requirements/required_fields.toml`はsource、merged、firestoreの全段階で`examYear`を必須にしている。
- `scripts/convert/convert_merged_to_firestore.py:get_exam_year`は`examYear`欠損を拒否し、変換結果へ常に`examYear`を出す。
- `scripts/upload/upload_questions_to_firestore.py:filter_question_fields`は欠損`examYear`を空文字で出力する。
- `scripts/common/repaso_firestore_schema.py`、`repaso/lib/firestore/models/question_doc.dart`、`repaso/firestore.rules`は`examYear`をoptionalとして扱うため、Repaso側のschema変更は不要である。
- `scripts/scrape/common.py`にはsite不変ID、URL fallback、canonical identity、画像保存の共通処理があり、新siteは既存のsite adapter方式へ追加できる。
- `rg`調査でPing-t実装は存在しない。新しい`scraper_type=pingt`が必要である。

## Ping-t CLF-C02の取得事実

- qualification: `aws-cloud-practitioner`
- source subject ID: `76`
- output list group: `ping-t-aws-clf-c02`
- 一覧は547件、22ページ、基本25件/ページで、安定問題IDと`/question_subjects/76/questions/<id>`を持つ。
- 詳細ページから分類、問題文、選択肢、正答、解説、参考URL、画像URLを取得できる。
- 演習sessionを作らず、検索一覧と問題詳細のGETだけで全件列挙できる。

## 未整合とリスク

1. 文書は05を実装済みのように読めるが、実装・工程設定・検証は未接続である。
2. 現行convertとrequired-fieldsは独自問題の`examYear` omitを拒否する。
3. 現行uploadは欠損fieldを空文字に変えるため、独自問題のomit契約に反する。
4. 認証必須siteの秘密情報はrepoへ保存せず、既存MEC Net.と同じsecure.env境界又はログイン済みブラウザからのread-only exportを使う必要がある。
5. 全547件の完全性は、一覧ID集合と保存ID集合の一致、ID一意性、必須field、正答数、画像参照、再取得no-opで検証する必要がある。
6. 既存のガス主任出力変更は今回のscope外であり、stage、commit、破棄しない。

## 最大の安全な最初のWorker package

Ping-tを資格非依存のsite adapterとして追加し、CLF-C02の547問を`00_source`へ取得して完全性を検証する。

### allowed files

- `scrape_pingt.py`
- `scripts/scrape/pingt.py`
- `scripts/scrape/run_qualification_scrape.py`
- `scripts/scrape/qualification_presets.py`
- `scripts/scrape/common.py`
- `config/scrape_presets.json`
- `document/sources/README.md`
- `document/sources/ping-t/ping-t_source_contract.md`
- `document/operations/scraping_workflow.md`
- `tests/test_scrape_pingt.py`
- `tests/test_scrape_presets.py`
- `tests/fixtures/pingt/**`
- `output/aws-cloud-practitioner/questions_json/ping-t-aws-clf-c02/00_source/**`
- `output/aws-cloud-practitioner/question_images/ping-t-aws-clf-c02/**`
- `output/aws-cloud-practitioner/reports/**`
- `document/temporary/audits/**ping*t**`
- `document/contracts/00_source_sha256_manifest.jsonl`

### verify

- `python3 -m unittest tests.test_scrape_pingt tests.test_scrape_presets tests.test_scrape_identity_keys tests.test_check_00_source_immutability`
- 保存547件、一覧547 IDとの集合一致、`source_question_id`重複0、問題URL重複0を確認する。
- 全問で問題文、2件以上の選択肢、1件以上の正答、解説本文を確認する。
- 画像参照URLとローカル画像の対応を確認する。
- 同じpresetを再実行し、新規保存0・既存変更0になることを確認する。
- `python3 scripts/check/check_00_source_immutability.py --record-new`後にimmutability checkを通す。

## Scout baseline

`python3 -m unittest tests.test_scrape_identity_keys tests.test_scrape_presets tests.test_convert_merged_to_firestore tests.test_upload_questions_to_firestore tests.test_question_review_workflow_catalog tests.test_documentation_structure`は82 testsすべて成功した。現状テストが目標未実装を検出していないため、独自問題fixtureの追加が必要である。
