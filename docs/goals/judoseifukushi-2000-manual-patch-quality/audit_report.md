# 2000年（第8回）柔道整復師国家試験 4層パッチ整備・監査レポート

## 1. 概要
- **年度**: 2000年（第8回）
- **対象資格**: 柔道整復師（`judoseifukushi`）
- **対象問題数**: 全200問（Part 1〜8、各25問）
- **整備レイヤー**:
  - `10_questionType_fixed`
  - `15_correctChoiceText_fixed`
  - `23_correctChoiceText_fixed`
  - `21_explanationText_added`

## 2. 特記事項・医学的根拠の整理
- **問45（ビタミンD関連・解なし）**: 全選択肢（紫外線、上皮小体、腎臓、くる病）がいずれもビタミンDに関連するため「解なし（採点除外・全員正解）」と判定された出題。
- **問188（トーマステストと股関節拘縮）**: 健側股関節を抱え込んだ際に患側大腿が浮き上がる所見（股関節屈曲拘縮）。
- **問198（膝関節複合損傷）**: 外転動揺性＋内側裂隙痛（MCL損傷）、前方引き出し陽性（ACL損傷）、内側半月損傷からなる「オドナヒューの不幸の三徴（Unhappy triad: 部位4, 5, 6）」。

## 3. 検証結果
- `python3 scripts/check/check_questiontype_patch_coverage.py --list-group-id 2000 --base-dir output/judoseifukushi/questions_json`
  - `question_2000_1.json` 〜 `question_2000_8.json`: 全て **[OK]**
- `00_source` 不変性チェック: 完全維持。
