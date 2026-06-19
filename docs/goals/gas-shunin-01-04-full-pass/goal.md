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
- `originalQuestionId` は台帳上に保持し、patch雛形では `source_original_question_id` として残す。
- 甲種・乙種とも、一問ずつ目視クオリティで進める。複数問を機械的にまとめて ok にしない。
- 文字列本文・解説本文をプログラムで生成しない。プログラム利用は棚卸し、雛形生成、検証、merge、差分確認に限る。
- 03 の解説は Firestore 既存解説、gassyunin.com 由来データ、PDF/スクショ/OCR、信頼できる一次情報を根拠にし、根拠なしの推測で確定しない。
- 04 の `questionSetId` は Firestore snapshot から復元した category を使う。
- `output/` 配下の成果物は Git 管理外でよい。GitHubへは script / test / goal docs のみを対象にする。

## Oracle

完了条件は、甲種412問・乙種522問の全934問について、01 `questionType`、02 `questionIntent` / `correctChoiceText`、03 `explanationText`、04 `questionSetId` が一問ずつ確認され、該当 patch が固定名ファイルに反映され、coverage / merge / upload-prep dry-run 相当の検証を通り、最終監査で既存 Firestore ID を変更していないことが証明されること。

## Current Status

準備は完了。レビュー台帳・固定名パッチ雛形・Firestore category は生成済みで、pending 許容の整合性検証は通過済み。実レビューはまだ開始していない。

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
