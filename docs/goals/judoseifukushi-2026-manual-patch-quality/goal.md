# 柔道整復師 2026年 目視整備 patch 品質向上

## Objective

`output/judoseifukushi/questions_json/2026/` 配下の全250問について、柔道整復学および関係法規の専門家・参考書著者の観点で1問ずつ目視で精査し、所定のパイプライン（第01工程: 問題形式、第02工程: 設問意図、第02a工程: 正答精査、第02b工程: 法令根拠、第03工程: 解説文・学び方分類）に沿って手作業で高品質に整備する。

## Original Request

「まず対象の問題を一覧化してgoalで一問ずつ目視整備した方が良い。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 柔道整復師国家試験の受験者、および問題データを活用する学習アプリケーション利用者
- Authority: `requested`
- Proof type: `artifact + verification + review`
- Completion proof: 対象250問が一問ずつ目視処理され、各設問で正しい回答、`questionType`、`questionIntent`、`correctChoiceText`、`explanationText`、`questionLearningPatternId` の整合が確認され、既存の公式チェッカー（`check_questiontype_patch_coverage.py`、`check-question-intent-patch`、`check_correct_choice_patch_coverage.py`、`check_explanation_patch_coverage.py`）をすべて通過して `origin/main` に反映されること
- Goal oracle: 2026年の全10ファイル（250問）について、`10_questionType_fixed/`、`15_correctChoiceText_fixed/`、`23_correctChoiceText_fixed/`、`21_explanationText_added/` が揃い、機械検証エラーゼロで完了すること
- Non-Negotiable Constraints:
  - 会話・報告は常に日本語で行う。
  - 変更時には作業内容と保存先を明示する。
  - 一括スクリプトによる自動生成・機械置換は禁止。既存ツールのみを活用し、1問ずつ問題文・全選択肢・公式正答を熟読・目視精査する。
  - `00_source` は不変保護（一切改変・削除しない）。
  - 日本語品質は文化庁「公用文作成の考え方」および良質な参考書・問題集水準を厳守し、文頭に結論（`正しい。` / `間違い。`）を置き、法令名ではなく説明対象の概念を主語に据える。
  - Git運用は単一 `main` ブランチ運用とし、`origin/main` へ直接コミット・プッシュする。

## Progress & Execution Order

- **問1〜問50（第1回〜第2回、計50問）**: ✅ **整備完了**（コミット `5b9c1226a`, `6d3649acc` にて `origin/main` 反映済）
- **問51〜問75（`question_2026_3.json`）**: ⏳ 次の着手対象
- **問76〜問100（`question_2026_4.json`）**: ⏳ 未着手
- **問101〜問125（`question_2026_5.json`）**: ⏳ 未着手
- **問126〜問150（`question_2026_6.json`）**: ⏳ 未着手
- **問151〜問175（`question_2026_7.json`）**: ⏳ 未着手
- **問176〜問200（`question_2026_8.json`）**: ⏳ 未着手
- **問201〜問225（`question_2026_9.json`）**: ⏳ 未着手
- **問226〜問250（`question_2026_10.json`）**: ⏳ 未着手

詳細な問題一覧表は [question_inventory.md](question_inventory.md) を参照。
