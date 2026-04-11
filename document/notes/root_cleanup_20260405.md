# Root Cleanup 2026-04-05

`exam_scraper` ルート直下の一時ファイルと文書ファイルを整理した記録。

## archive に退避したファイル

- `archive/root_cleanup_20260405/debug_compare_strings.py`
- `archive/root_cleanup_20260405/generate_patch.py`
- `archive/root_cleanup_20260405/inject_logic.py`
- `archive/root_cleanup_20260405/link_questions.py`
- `archive/root_cleanup_20260405/patch_file_job.py`
- `archive/root_cleanup_20260405/patch_phase2.py`
- `archive/root_cleanup_20260405/patch_runner.py`
- `archive/root_cleanup_20260405/test.json`
- `archive/root_cleanup_20260405/test_dummy_file.txt`
- `archive/root_cleanup_20260405/test_validator.py`
- `archive/root_cleanup_20260405/update_pipeline.py`
- `archive/root_cleanup_20260405/update_runner.py`

## document に移したファイル

- `datamodel` -> `document/reference/firestore_datamodel.md`
- `memo` -> `document/notes/legacy_root_memo.md`

## そのまま残したファイル

- `requirements_firestore.txt`
  Firestore upload 用の最小依存定義として現行ドキュメントから参照されているため、今回はルート直下に残した。

## 判断メモ

- `link_questions.py` は旧運用スクリプトとして archive に隔離した。
  現行方針では `questionSetId` は AI パイプラインで扱い、このスクリプトは `folderId` を許容していて現行ルールと衝突するため。
- 退避対象は Git 管理外ワークスペースであることを踏まえ、削除ではなく archive に移動した。

## スキル文書の所在

- 過去問整形作業の主スキル正本はリポジトリ内 `skills/exam-firestore-pipeline/` に集約した。
- 主スキル本体:
  `/Users/yuki/development/exam_scraper/skills/exam-firestore-pipeline/SKILL.md`
- Codex の既定読込先:
  `/Users/yuki/.codex/skills/exam-firestore-pipeline`
  これは上記リポジトリ内ディレクトリへの symlink に切り替えた。
- プラグイン由来の skill:
  `/Users/yuki/.codex/plugins/.../skills/*/SKILL.md`
