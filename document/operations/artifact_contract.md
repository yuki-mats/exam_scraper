# 問題整備artifact契約

この文書は、問題整備で作るディレクトリ、ファイル名、所有工程の正本です。fieldの中身は[question field契約](../reference/question_field_contract.md)、作業手順は[prompt一覧](../../prompt/README.md)を参照してください。

## ディレクトリ構成

```text
output/<qualification>/
  question_images/<exam_occurrence_id>/
  questions_json/<exam_occurrence_id>/
    00_source/
    10_questionType_fixed/
    12_merged_questionType/
    15_correctChoiceText_fixed/
    18_law_context_prepared/
    20_merged_1/
    21_explanationText_added/
    22_questionSetId_linked/
    23_correctChoiceText_fixed/
    24_questionIssueCorrections/
    30_merged_2/
    40_convert/
    99_model_review_flags/
  questions_json/upload_to_firestore/
  category/category.json
  law_evidence/<exam_occurrence_id>/
  reports/
output/question_review_console/
  <qualification>/<listGroupId>/
    work_versions.json
    evaluations/
    reviews/
  workflow_runs/<qualification>/<runId>/
  work_version_backfills/<timestamp>/manifest.json
  publish_runs/<qualification>/<runId>/
```

`<qualification>`は人が読めるkebab-caseのローカル資格コードとします。本番Firestoreで既存`qualificationId`を維持する必要がある場合は、`config/scrape_presets.json`の`publication_qualification_id`へ分離します。

`<exam_occurrence_id>`は過去問年度を表します。年度内に試験区分が1つなら`YYYY`、複数ある資格では`YYYY01`を前期、`YYYY02`を後期として保存します。取得元siteのgroup IDは`source_list_group_id`にだけ保持し、ディレクトリ名へ流用しません。

## 所有工程

`<source_stem>`は`00_source`の拡張子を除いた名前です。日常運用では固定名を更新し、同じ工程のtimestamp付きファイルを増やしません。

| 工程 | 保存先 | ファイル名 | 責務 |
| --- | --- | --- | --- |
| scrape | `00_source/` | `question_<source又はexam occurrence ID>_<n>.json` | 取得した原本。既存ファイルの内容と名前は不変。 |
| scrape | `question_images/<exam_occurrence_id>/` | source由来名 | ローカル画像。 |
| 01 | `10_questionType_fixed/` | `<source_stem>_questionType_fixed.json` | 問題形式。 |
| merge | `12_merged_questionType/` | `<source_stem>_merged.json` | 01反映確認用の生成view。 |
| 02 | `15_correctChoiceText_fixed/` | `<source_stem>_merged_correctChoiceText_fixed.json` | 互換名を維持した`questionIntent` patch。 |
| 02a | `23_correctChoiceText_fixed/` | `<source_stem>_merged_correctChoiceText_fixed.json` | 03前に確定する厳密正答patch。 |
| 02b | `18_law_context_prepared/` | `<source_stem>_merged_lawContext_prepared.json` | 法令関連性と根拠候補。 |
| merge | `20_merged_1/` | `<source_stem>_merged.json` | 03・04の主入力。02aと02bも反映する。 |
| 03 | `21_explanationText_added/` | `<source_stem>_merged_explanationText_added.json` | 解説、想定質問、法令監査facts。 |
| 03c | `output/<qualification>/category/` | `category.json` | 資格全体の分類正本。問題単位patchではない。 |
| 04 | `22_questionSetId_linked/` | `<source_stem>_merged_questionSetId_linked.json` | `category.json`に基づく問題集対応。 |
| 問題報告 | `24_questionIssueCorrections/` | `<batch>_<work>_<originalQuestionId>.json` | blind review済みcorrection overlay。 |
| merge | `30_merged_2/` | `<source_stem>_merged_<timestamp>.json` | upload前の全patch統合結果。 |
| convert | `40_convert/` | `<list_group_id>_firestore_<timestamp>.json` | Firestore schemaへの変換結果。 |
| delivery | `upload_to_firestore/` | `<list_group_id>_firestore_<timestamp>.json` | upload対象の正規artifact。 |
| uncertainty | `99_model_review_flags/` | `<source_stem>_<stage>_needs_5_5_high_review.jsonl` | patchへ混ぜない未確認事項。 |

## 補助artifact

| 種類 | 保存先 | 性質 |
| --- | --- | --- |
| category | `output/<qualification>/category/category.json` | `questionSetId`の分類正本。 |
| law snapshot | `output/<qualification>/law_evidence/<list_group_id>/` | 条文本文・hashなどの監査用evidence。 |
| law audit | `output/<qualification>/review/law_revision_audit/` | queue、sidecar、監査結果。 |
| generated reports | `output/<qualification>/reports/` | checkerやmigrationの再生成可能なreport。 |
| review | `output/question_review_console/<qualification>/<listGroupId>/reviews/` | 人間の指摘とCodex依頼。 |
| work version | `output/question_review_console/<qualification>/<listGroupId>/work_versions.json` | 問題ごとの工程版履歴。patch又はFirestore fieldではない。 |
| session run | `output/question_review_console/workflow_runs/<qualification>/<runId>/` | Codex App Serverで実行した整備・評価・再整備の`manifest.json`、`prompt.md`、`result.json`。整備系は再起動回収用`baseline.json`と`agent_output/result.json`を持つ。 |
| evaluation projection | `output/question_review_console/<qualification>/<listGroupId>/evaluations/` | 元問題単位の最新評価。promptは同階層の`evaluation_prompts/`。 |
| work version backfill | `output/question_review_console/work_version_backfills/<timestamp>/manifest.json` | 公開済み問題をlegacy `v0.0`へ初期化した対象、照合結果、件数のreceipt。 |
| work version migration | `output/question_review_console/work_version_migrations/<timestamp>/manifest.json` | 既存工程版を`MAJOR.MINOR`形式へ移行した件数と保存先のreceipt。 |
| publish run | `output/question_review_console/publish_runs/<qualification>/<runId>/` | preflight、対象artifact、result、readback。 |

session runは`workType`、`sessionId`、`threadId`、`turnId`、対象、`stateHash`、`policyVersions`、`policyFingerprints`、`policyTargets`、sandbox、時刻、状態をmanifestへ保存し、成功時だけ`workVersionReceipt`を持ちます。run directoryは再利用しません。`evaluations/`はUI向けの最新projectionに限定し、評価内容は[`evaluation_result.schema.json`](../../tools/question_review_console/evaluation_result.schema.json)、作業版の意味、有効性、公開条件は[問題整備システム](local_question_review_console.md)を正本とします。

## 編集境界

- 手作業で編集するのはpatchと承認対象の設定だけとする。
- `00_source`の親ディレクトリは、資格コード又は年度・試験区分を整理する移行に限り、file hashと`00_source/`以下の相対名を保持して移動できる。移行後はimmutability manifestへ明示登録する。
- `12`、`20`、`30`、`40`、`upload_to_firestore`は生成物であり、直接修正しない。
- 不確実性と監査履歴をFirestore用recordへ未知fieldとして混ぜない。
- 品質確認artifactと公開flagはreview専用であり、patch、merged、Firestore question documentへ混ぜない。
- `work_versions.json`はserverだけが検証済みreceipt又は明示的なbackfillから更新し、patch、merged、upload-ready、Firestore question documentへ複製しない。
- 新fieldを公開artifactへ入れる前に、field契約、repaso schema、convert、upload、quality-gateを同時に更新する。
- 正誤が02a又は03bで変わった場合は`23`を更新し、`20`と03以降を再生成する。
