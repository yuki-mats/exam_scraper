# ガス主任技術者 甲種・乙種 01-04 manual review

## Objective

ガス主任技術者試験の甲種・乙種について、`00_source` を起点に 01〜04 prompt workflow を一問ずつ進められる状態にする。Firestore に既に存在する問題は Firestore の既存 document ID / `originalQuestionId` を正本として保持し、gassyunin.com 由来の問題と衝突しない `reviewQuestionId` で手作業レビューを管理する。

## Original Request

ガス主任技術者試験においても 01〜04 prompt の作業をできるようにしたい。甲種、乙種に関して一問一問、精度を高く目視クオリティで実施したい。必要な準備をまずしてほしい。

## Scope

- 甲種: `output/gas-shunin-kou/questions_json` の 2019〜2025、412問
- 乙種: `output/gas-shunin-otsu/questions_json` の 2017〜2025、522問
- 合計: 934問
- 台帳:
  - `output/gas-shunin-kou/review/01_04_manual_review/gas-shunin-kou_01_04_manual_review.jsonl`
  - `output/gas-shunin-otsu/review/01_04_manual_review/gas-shunin-otsu_01_04_manual_review.jsonl`
- category:
  - `output/gas-shunin-kou/category/category.json`
  - `output/gas-shunin-otsu/category/category.json`

## Non-Negotiable Constraints

- 会話・報告は日本語で行う。
- `00_source` の問題文・選択肢・既存本文はこの review workflow 中に直接書き換えない。
- 既存 Firestore の `questionId` / `originalQuestionId` は変更しない。
- Firestore 由来の既存問題は `firestoreQuestionIds` から作った `reviewQuestionId` をレビュー・patch適用キーにする。
- Firestore upload 用の `questions[].questionId` は、既存問題では `firestoreQuestionIds[index]` を使う。`sourceQuestionKey` / `sourceUniqueKey` は照合・merge用キーであり、既存Firestore document IDの置換には使わない。
- gassyunin.com / PDF / OCR 由来の新規問題だけ、`sourceUniqueKey` から決定的な新規 `questionId` を作る。既存Firestoreと一致する `sourceUniqueKey` がある場合は、必ず既存doc IDへ解決する。
- `originalQuestionId` は台帳上に保持し、patch雛形では `source_original_question_id` として残す。
- 甲種・乙種とも、一問ずつ目視クオリティで進める。複数問を機械的にまとめて ok にしない。
- `originalQuestionBodyText` / `originalQuestionChoiceText` と `00_source` の本文・選択肢は、過去問通りの正本として生成・改変しない。
- 03 の `explanationText` / suggested 系は生成・補完してよい。ただし、Firestore 既存解説、gassyunin.com 由来データ、PDF/スクショ/OCR、信頼できる一次情報、または目視で確認できる正誤根拠に基づけ、根拠なしの推測で確定しない。
- 04 の `questionSetId` は Firestore snapshot から復元した category を使う。
- `output/` 配下の成果物は通常 Git 管理外だが、この goal の review ledger / 固定名 patch を進捗証跡としてコミット対象にする場合は対象ファイルだけを `git add -f` で限定する。

## Oracle

完了条件は、甲種412問・乙種522問の全934問について、01 `questionType`、02 `questionIntent` / `correctChoiceText`、03 `explanationText`、04 `questionSetId` が一問ずつ確認され、該当 patch が固定名ファイルに反映され、coverage / merge / upload-prep dry-run 相当の検証を通り、最終監査で既存 Firestore ID を変更していないことが証明されること。

## ID Policy

- `sourceQuestionKey`: 問題単位の自然キー。形式は `gas-shunin:{grade}:{year}:{subject}:q{questionNo}`。
- `sourceUniqueKey`: 選択肢・設問単位の自然キー。形式は `gas-shunin:{grade}:{year}:{subject}:q{questionNo}:s{statementNo}`。
- `reviewQuestionId`: 01〜04のreview/patch照合キー。既存Firestore由来では `firestore:<doc ids>`、サイト由来では `publicQuestionId` または `sourceUniqueKey`。
- `questionId`: Firestore `questions` のdocument ID。既存Firestore由来では絶対に既存doc IDを使う。
- `originalQuestionId`: Firestore既存フィールド。既存値を維持する。

`sourceQuestionKey` / `sourceUniqueKey` は、Firestore・gassyunin.com・PDF/OCRの同一問題照合に使う。既存Firestore document IDを直接置き換える用途には使わない。

## Current Status

準備は完了。レビュー台帳・固定名パッチ雛形・Firestore category は生成済みで、pending 許容の整合性検証は通過済み。甲種2019問17・問18はレビュー済み。全934問の一問単位実行計画は `docs/goals/gas-shunin-01-04-full-pass/notes/question-plan/` に保存済み。

## Next Command

```text
/goal Follow docs/goals/gas-shunin-01-04-full-pass/goal.md.
```

## PM Loop

1. `state.yaml` の active task だけを扱う。
2. Judge が最初の安全な一問を確定する。
3. Worker は一問だけを対象に、対象 `reviewQuestionId`、source、patch file、検証コマンドを明示してから編集する。
4. 01〜04 の各欄を確認し、台帳の該当行を `pending` から更新する。
5. file-level coverage と必要な merge 検証を実行する。
6. receipt を残して次の一問へ進める。
