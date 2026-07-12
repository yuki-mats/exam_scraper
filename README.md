# exam_scraper

試験問題を取得し、patchで整備し、Firestore向けartifactへ変換するrepositoryです。

## 最初に読む文書

[問題整備ワークフロー](document/operations/exam_pipeline_manual_and_automation.md)を唯一の入口とします。工程、保存先、field、法令監査、レビューUI、公開の詳細は、そこから各SSOTへ進んでください。

## 主なディレクトリ

| 場所 | 役割 |
| --- | --- |
| `config/` | 資格presetと機械要件。 |
| `scripts/` | scrape、merge、convert、uploadの実装。 |
| `tools/` | 日常運用のCLIとレビューUI。 |
| `prompt/` | 人間・AIによるpatch作業の正本prompt。 |
| `document/operations/` | 継続更新するworkflow仕様。 |
| `document/reference/` | fieldなどの共通契約。 |
| `document/sources/` | source固有の継続契約と取得資料。 |
| `document/temporary/` | 日付付き監査・レビュー・移行記録。 |
| `docs/goals/` | GoalBuddyの一時的な実行記録。仕様正本ではない。 |
| `tests/` | 自動テスト。 |
| `output/` | ローカル生成物。Git管理しない。 |

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_firestore.txt
python3 -m pip install requests beautifulsoup4
```

Firestore / Storageを使う場合は、service accountをrepositoryへ置かず環境変数で指定します。

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

既存と同じ公開question IDを生成する作業では、従来値の`QUESTION_ID_SECRET_KEY`が必要です。

```bash
export QUESTION_ID_SECRET_KEY=your-existing-secret
```

公開手順と安全境界は[delivery workflow](document/operations/delivery_workflow.md)を参照してください。

## テスト

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
