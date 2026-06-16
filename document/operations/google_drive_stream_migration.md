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

## 2026-06-16 phase 2

実施済み:

- `output` 全体の symlink 化は避けた。
  - 理由: `git ls-files output` で 4,615 件の tracked file があり、`output` ディレクトリ全体を symlink 化すると tracked file が削除扱いになる。
- tracked file を含まない重い生成物だけを Drive stream 側へ移動し、元パスには absolute symlink を作成。
- `output/2nd-class-kenchikushi/questions_json/upload_to_firestore` は tracked file が 1 件あるため移動対象から除外。

Drive stream 側の外部出力置き場:

```bash
/Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ/400_アプリ開発・運営/exam_scraper_external_output
```

移動対象:

```bash
output/*/question_images
output/*/questions_json/upload_to_firestore
```

ただし、tracked file を含むディレクトリは除外。

移動後のサイズ:

```bash
du -sh output exam_scraper_external_output .
```

結果:

- `output`: 1.1G
- `exam_scraper_external_output`: 1.4G
- repo 全体: 4.4G

移動前の参考値:

- `output`: 2.5G
- repo 全体: 5.7G

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
.venv/bin/python scripts/scrape/run_qualification_scrape.py anma --dry-run
```

結果: 既存 `00_source` を検出し、実行対象なしで完了。

symlink 経由の読み取り確認:

```bash
find -L output/mecnet-kokushi/question_images -type f | wc -l
find -L output/mecnet-kokushi/questions_json/upload_to_firestore -type f | wc -l
```

結果:

- `output/mecnet-kokushi/question_images`: 3,317 files
- `output/mecnet-kokushi/questions_json/upload_to_firestore`: 52 files

Git 状態への影響:

```bash
git status --short | awk '{print $1}' | sort | uniq -c
```

移動前後とも `142 D` / `54 M` で変化なし。symlink 化による追加 tracked 差分はなし。

phase 2 rollback:

```bash
DRIVE_ROOT="/Users/yuki/Library/CloudStorage/GoogleDrive-yuki.matsuda007@gmail.com/マイドライブ/400_アプリ開発・運営/exam_scraper_external_output"
for link in output/*/question_images output/*/questions_json/upload_to_firestore; do
  [ -L "$link" ] || continue
  target=$(readlink "$link")
  case "$target" in
    "$DRIVE_ROOT"/*)
      rm "$link"
      mv "$target" "$link"
      ;;
  esac
done
```

次に進める前の注意:

- `output` 全体を Drive stream へ移すには、tracked output files の整理方針が必要。
- 本体 repo の移動は、`output` の tracked file と `.git` の扱いを決めてから行う。

## 2026-06-16 phase 3

実施済み:

- 本体移行前の容量削減として `git gc --prune=now` を実行。

実行前:

```bash
git count-objects -vH
du -sh .git .
```

結果:

- loose objects: 9,541
- loose object size: 2.36 GiB
- pack size: 262.22 MiB
- `.git`: 2.6G
- repo 全体: 4.4G

実行後:

```bash
git count-objects -vH
du -sh .git .
```

結果:

- loose objects: 0
- loose object size: 0 bytes
- pack size: 329.37 MiB
- `.git`: 331M
- repo 全体: 2.1G

Git 状態への影響:

```bash
git status --short | awk '{print $1}' | sort | uniq -c
```

結果は `142 D` / `54 M` のままで、`git gc` による working tree 差分はなし。

次の候補:

- tracked output files のうち、今後も Git 管理すべきものと生成物として外すものを分類する。
- 本体 repo を Drive stream へコピーし、旧パスを symlink にする検証へ進む。
