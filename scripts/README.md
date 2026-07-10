# scripts

`exam_scraper` の内部実装・互換入口・個別補助スクリプト置き場です。

日常運用で直接使う入口は [tools/question_bank](/Users/yuki/development/exam_scraper/tools/question_bank/README.md) です。全体手順は [document/operations/exam_pipeline_manual_and_automation.md](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md) を正本にします。

## ディレクトリ

- `scrape/`
  - `config/scrape_presets.json` の資格プリセットを使って、サイト別スクレイパを実行する。
  - 出力は `output/<qualification>/questions_json/<list_group_id>/00_source/` と `question_images/<list_group_id>/`。
  - `kakomonn.com` 全体の未対応資格を棚卸しする場合:
    `python scripts/scrape/kakomonn_inventory.py inventory --discover-targets --only-missing --json-output output/reports/kakomonn_inventory.json`
  - `kakomonn.com` の未登録資格を config 追加なしで試し取得する場合:
    `python scripts/scrape/kakomonn_inventory.py scrape itpass --dry-run --max-groups 1`
    取得時は `--dry-run` を外す。大量取得は `--all-missing --max-qualifications <n>` で小さく区切る。
- `merge/`
  - `10_questionType_fixed/`、`15_correctChoiceText_fixed/`、`18_law_context_prepared/`、`21_explanationText_added/`、`22_questionSetId_linked/`、`23_correctChoiceText_fixed/` を統合する。
  - 主な生成先は `12_merged_questionType/`、`20_merged_1/`、`30_merged_2/`。
- `convert/`
  - merged JSON を Firestore schema 向け JSON へ変換する。
- `pipeline/`
  - merge、convert、category count 更新、upload dry-run などをまとめて実行する。
- `check/`
  - patch coverage、required fields、questionSetId、Firestore schema などの実装を置く。
  - 日常チェックは `python tools/question_bank/question_bank.py quality-gate ...` を使う。
- `fix/`
  - patch 作業の退避、最小 JSON の正式化、画像 URL 正規化などの補助処理を置く。
  - `fix/README.md` に使用可否と注意点を明記する。
- `upload/`
  - Storage / Firestore への upload と dry-run を行う。
  - `upload_questions_to_firestore.py` は questions upload 成功時に `qualificationId` ごとの `examYear` を集計し、Repaso 公開 config の `official_exam_years_by_qualification` へ追記する。アプリ側の年度チップ候補はこの manifest を優先し、問題 document の `examYear` は従来通り正本として保持する。
- `old/`
  - Git 管理外の legacy local scripts 置き場。日常運用の正本にはしない。

## 運用ルール

- `output/`、`tmp_ai_raw/`、`archive/`、`scratch/` は生成物または一時作業用であり、Git 管理しない。
- patch 本文を量産する目的で Python を使わない。厳密レビューでは Codex/Worker が対象資格の専門家・問題作成者・参考書著者の観点で一問ずつ本文を判断する。
- スクリプトで許容するのは、退避、最小 JSON の補完、merge、convert、検証、upload dry-run、本番 upload。
- ユーザー向けの新しい日常コマンドを増やす場合は、まず `tools/question_bank/question_bank.py` のサブコマンドとして追加する。
- 本番 upload は dry-run と schema validation を通した後に実行する。
