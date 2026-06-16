# Google Drive stream migration

この文書は、`exam_scraper` を Google Drive for desktop の stream 配下で扱えるようにする段階移行メモです。

## 方針

- repo ルートは移動しても動くように、実行系の絶対パスを repo-root 相対へ寄せる。
- `output/`、`.git/`、`.venv/` などの重いディレクトリは、最初から本移行しない。
- 先に Drive stream 上の軽量コピーでテストし、問題がなければ本体移行へ進む。
- 旧パス `/Users/yuki/development/exam_scraper` は、最後に symlink で互換維持する。

## Drive stream location

現在の Google Drive stream ルート:

```bash
/Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ
```

検証コピー:

```bash
/Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ/400_アプリ開発・運営/exam_scraper_migration_probe
```

## 2026-06-16 phase 1

実施済み:

- 実行系 Python の `/Users/yuki/development/exam_scraper/output` 直書きを repo-root 相対へ変更。
- `tests/test_question_count_grouping.py` の module load path を repo-root 相対へ変更。
- Drive stream 上へ軽量検証コピーを作成。

検証済み:

```bash
.venv/bin/python -m unittest tests.test_question_count_grouping tests.test_scrape_presets
```

結果: 32 tests OK

```bash
cd /Users/yuki/development
exam_scraper/.venv/bin/python -m pytest \
  exam_scraper/tests/test_question_count_grouping.py \
  exam_scraper/tests/test_scrape_presets.py
```

結果: 32 passed

```bash
/Users/yuki/development/exam_scraper/.venv/bin/python -m py_compile \
  code.py scrape_gassyunin.py scrape_sgsiken.py scrape_kurohon.py scrape_mecnet_kokushi.py \
  scripts/merge/02_merge_questiontype.py \
  scripts/fix/archive_patch_outputs.py \
  scripts/fix/remove_answer_result_debug_fields.py \
  scripts/fix/fill_question_intent_15_correctChoiceText_fixed_inplace.py \
  scripts/fix/fix_15_correctChoiceText_fixed_inplace.py \
  scripts/fix/migrate_correct_choice_patches_23_to_15.py \
  tests/test_question_count_grouping.py
```

結果: OK

Drive stream 検証コピー上:

```bash
cd "/Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ/400_アプリ開発・運営/exam_scraper_migration_probe"
/Users/yuki/development/exam_scraper/.venv/bin/python -m py_compile ...
/Users/yuki/development/exam_scraper/.venv/bin/python scripts/scrape/run_qualification_scrape.py anma --dry-run
```

結果: py_compile OK。`anma` dry-run は 2016 から 2026 の 11 group を plan 表示。

```bash
cd "/Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ/400_アプリ開発・運営"
/Users/yuki/development/exam_scraper/.venv/bin/python -m pytest \
  exam_scraper_migration_probe/tests/test_question_count_grouping.py \
  exam_scraper_migration_probe/tests/test_scrape_presets.py
```

結果: 32 passed

## Known issue

repo ルートを cwd にして `pytest` を起動すると、ルート直下の `code.py` が Python 標準ライブラリ `code` を隠し、pytest の pdb import が失敗する。

回避策:

```bash
cd /Users/yuki/development
exam_scraper/.venv/bin/python -m pytest exam_scraper/tests/...
```

これは Google Drive 移行とは別の既存問題。後続で `code.py` の改名を検討する。

## Next phase

1. phase 1 のコード変更を commit / push する。
2. `output/` の扱いを決める。
   - 本体 repo と一緒に Drive stream へ置く。
   - または `output/` だけ Drive stream へ置き、repo 側は symlink にする。
3. 本体移行前に `git gc` で `.git` を整理する。
4. 本体を Drive stream へコピーする。
5. `/Users/yuki/development/exam_scraper` を退避し、Drive stream 側への symlink を作る。
6. symlink 経由と Drive 実体経由の両方でテストする。

ロールバック:

```bash
rm /Users/yuki/development/exam_scraper
mv /Users/yuki/development/exam_scraper.before-drive-stream /Users/yuki/development/exam_scraper
```
