# exam_scraper

試験問題データを取得し、Firestore 向け JSON へ変換・アップロードするためのスクリプト群です。

## 管理対象

- `code.py`: 問題ページの取得処理
- `scripts/`: 変換、検証、マージ、Firestore/Storage アップロード用スクリプト
- `tests/`: 変換・検証処理のテスト
- `config/`: 資格ごとの設定
- `document/`: 運用メモとデータモデル
- `prompt/`: AI 補正用プロンプト

`output/`、`tmp_ai_raw/`、`.cache/`、`archive/`、`.venv/` は生成物またはローカル環境として Git 管理から除外します。

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
