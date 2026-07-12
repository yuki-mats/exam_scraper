# T001 問題整備workflow map

## 正本

- `prompt/README.md`: 01 → 02 → 02b → 03 → 03b → 04の目視判断順序。
- `document/operations/exam_pipeline_manual_and_automation.md`: 人間判断とスクリプト責務、artifact、quality-gate、法令分岐のend-to-end正本。
- `document/operations/local_question_review_console.md`: 現行レビューUIの責務と安全境界。
- `document/reference/question_field_contract.md`: patch・Firestore field契約。
- `prompt/qualification_docs/<qualification>/`: 資格固有の法令範囲・解説・category方針。現状は一部資格だけに存在する。

## Workflowとartifact

| 作業 | 正本prompt | 主artifact | 判断主体 |
| --- | --- | --- | --- |
| Scrape | site/preset | `00_source` | scraper + source確認 |
| 01 | `01_prompt_fix_questionType.md` | `10_questionType_fixed` | 人間目視相当 |
| 02 | `02_prompt_fix_questionIntent.md` | `15_correctChoiceText_fixed` | 人間目視相当 |
| 02b | `02b_prompt_prepare_law_context.md` | `18_law_context_prepared` | 人間・一次情報 |
| 03 | `03_prompt_add_explanationText.md` | `21_explanationText_added` | 人間目視相当 |
| 03b | `03b_prompt_audit_current_law_and_patch.md` | law audit sidecar + `lawRevisionFacts` | 一次・二次・三次監査 |
| 04 | `04_prompt_link_questionSetId.md` | `22_questionSetId_linked` | categoryとの意味判断 |
| correction | correction contract | `23_correctChoiceText_fixed` / `24_questionIssueCorrections` | 厳密レビュー |
| propagate | unified CLI | `30_merged_2` → `40_convert` → `upload_to_firestore` | scripts |
| verify | `question_bank.py quality-gate` | reports / dry-run | scripts + human hold解消 |

## 現行状態検出

- `QuestionInventory`は各`00_source` recordへ最新patchを投影し、merged・converted・upload-readyとの差分とissueをquestion単位で検出する。
- stage mapは`10/15/18/21/22/23`を認識するが、UIのworkflow表示は`Patch/Merge/Convert/upload-ready/Firestore`の5段階へ圧縮されている。
- patchが存在しても品質承認済みとは限らない。`review ledger`、`hold`、法令監査state、freshness、coverageを別に見る必要がある。
- 新規資格の例として`1st-class-kenchikushi/2025`は125問すべてsourceのみで、required/identity/merge missing。既存資格`gas-shunin-otsu/2017`は58問のlocal artifactが一致しているが16問に法令監査metadata不足がある。`anma/2026`は160問にmerge/convert鮮度差がある。
- `00_source`とのstage別1:1 coverageは新規資格の未作業判定に使えるが、既存資格の再監査・洗い替えにはaudit method、ledger、artifact mtime/hashが必要。

## 現行UI/API

- `/api/inventory`はqualificationとlistGroupId一覧のみ。資格単位のstage summary、目的、次アクションはない。
- `/api/questions`は全group集約表示ができるが、問題一覧中心でworkflow overviewではない。
- UIは例外優先のqueue/detail構成。問題詳細からpatch同期、Firestore readback、明示確認付きpublish、review prompt生成、限定的直接編集ができる。
- `ArtifactSynchronizer`は単一groupのmerge/convert/upload-ready/upload dry-runを実行できるが、01〜04・02b・03bの対象抽出・prompt実行・進捗追跡は扱わない。
- `JobManager`は同一keyの並行実行を防ぐがin-memoryで、サーバー再起動後のresumeはない。
- 本番publishはpreview token、blocking issues、確認checkbox、readbackで保護されている。この境界は維持する。

## UX上の主要gap

1. 資格を選んだ直後に「どこまで完了し、なぜ止まり、次に何をするか」が分からない。
2. stageの目的と正本文書への導線がUIにない。
3. 新規・未作業・再実行・洗い替えが同じissue一覧へ混ざる。
4. 01〜04/02b/03bは対象ファイルとpromptを運用者が別途組み立てる必要がある。
5. qualification全体の進捗、question数、hold、freshness、実行履歴が一画面で追えない。

## 検証surface

- `python -m unittest discover -s tests -p 'test_question_review_*.py'`
- `tests/test_question_review_inventory.py`
- `tests/test_question_review_server.py`
- `tests/test_question_review_workflow.py`
- `node --check tools/question_review_console/static/app.js`
- `python -m py_compile tools/question_review_console/*.py`
- `git diff --check`
- 実データの`gas-shunin-otsu`とsource-only資格、fixtureのAPI/readback。
- in-app Browserでdesktop/mobile、console error、主要actionを確認。

## 最大安全slice候補

1. 共通workflow catalog + qualification overview API + シンプルな「次の作業」UIを縦断実装する。正本文書への参照と対象数を出し、既存単問queueへ自然に降りる。
2. stage別対象抽出と資格単位Codex prompt生成を追加する。prompt本文は正本を参照し、対象pathだけを固定する。
3. 安全なローカル実行をstage actionへ統合する。機械工程はjob実行、人間判断工程はprompt/receiptで追跡する。
4. durable run history/resume、洗い替え基準、qualification onboarding不足を追加する。
5. desktop/mobile browser polishと最終oracle auditを行う。

最初のsliceは1が適切。表示だけで終わらず、状態モデル・API・UI・テストを一つのvertical sliceで完成させる。
