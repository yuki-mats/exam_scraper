# 2026-06-20 upload dry-run ready

- 実行コマンド: `.venv/bin/python scripts/pipeline/prepare_firestore_upload.py kougai --base-dir output/kougai/questions_json --category-json output/kougai/category/category.json --questionset-only --dry-run`
- 2010-2025 の 16 年分を解決できた。
- dry-run 上の uploadable 生成先が全年度で投影できた。
- `unuploadable_total: 0` を確認した。
- 実ファイルの変更は発生していない。
