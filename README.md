# exam_scraper

試験問題データを取得し、Firestore 向け JSON へ変換・アップロードするためのスクリプト群です。

## 運用ドキュメント

スクレイピングから Firestore upload までの正本フローは次を参照してください。

- [過去問整備の統一CLI](/Users/yuki/development/exam_scraper/tools/question_bank/README.md)
- [運用ドキュメント一覧](/Users/yuki/development/exam_scraper/document/operations/README.md)
- [全体フロー](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md)
- [goal 駆動の日次更新フロー](/Users/yuki/development/exam_scraper/document/operations/goal_driven_update_workflow.md)
- [手作業 patch 品質向上 goal テンプレート](/Users/yuki/development/exam_scraper/docs/goals/templates/manual-patch-quality/README.md)

## 管理対象

- `code.py`: 問題ページの取得処理
- `tools/`: 日常運用で直接使う統一CLI
- `scripts/`: 変換、検証、マージ、Firestore/Storage アップロード用の内部実装・互換入口
- `tests/`: 変換・検証処理のテスト
- `config/`: 資格ごとの設定
- `document/`: 運用メモとデータモデル
- `prompt/`: AI 補正用プロンプト

`output/`、`tmp_ai_raw/`、`.cache/`、`archive/`、`.venv/` は生成物またはローカル環境として Git 管理から除外します。

## 安定運用の基本単位

通常の更新は `qualification_code` と `list_group_id` を指定して進めます。

- 取得更新: scrape preset から `00_source/` と画像を更新する
- 品質更新: `10_questionType_fixed/`、`15_correctChoiceText_fixed/`、`23_correctChoiceText_fixed/`、`21_explanationText_added/` などの patch を、対象資格の専門家・問題作成者・参考書著者の観点で一問ずつ見直す
- 公開準備: `00_merge_all.py` と `prepare_firestore_upload.py` で `30_merged_2/`、`40_convert/`、`upload_to_firestore/` を更新する
- 公開: Storage 画像、category、questions の順に dry-run してから upload する

各工程の機械チェックは、個別 script を探さず次を入口にします。

```bash
python3 tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification_code> \
  --list-group-id <list_group_id>
```

厳密な正答精度を狙う品質更新では、`docs/goals/templates/manual-patch-quality/` をコピーし、`1 Worker = 1問` で進めます。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_firestore.txt
```

スクレイピング処理には `requests` と `beautifulsoup4` も必要です。

```bash
python3 -m pip install requests beautifulsoup4
```

## Firestore / Storage 認証

サービスアカウント JSON は Git に入れず、次のどちらかで指定してください。

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

または各アップロードコマンドで指定します。

```bash
python3 scripts/upload/upload_questions_to_firestore.py --credentials-json /path/to/service-account.json
python3 scripts/upload/upload_category_to_firestore.py output/2nd-class-kenchikushi/category/category.json --licenseName example --upload --credentials-json /path/to/service-account.json
python3 scripts/upload/upload_question_images_to_storage.py 2nd-class-kenchikushi --credentials-json /path/to/service-account.json
```

## 一括アップロード（全資格）

ローカルの生成物（`upload_to_firestore` と `category.json` の `questionCount` など）をまず最新化してから、
全資格を Firestore へアップロードするには以下を実行します。

```bash
python3 scripts/pipeline/upload_all_to_firestore.py
```

`upload_questions_to_firestore.py` は差分がないドキュメントの書き込みをスキップし、`updatedAt` を更新しません。

## 公開用 question ID の秘密キー

`code.py` の `public_question_id` 生成には `QUESTION_ID_SECRET_KEY` が必要です。
既存データと同じ ID を維持したい場合は、これまで使っていた値をローカル環境変数に設定してください。

```bash
export QUESTION_ID_SECRET_KEY=your-existing-secret
```

## テスト

```bash
python3 -m unittest discover -s tests
```
