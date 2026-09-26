# 2019年（第27回）あん摩マッサージ指圧師国家試験 4層パッチ整備・監査レポート

## 1. 概要
- **年度**: 2019年（第27回）
- **対象資格**: あん摩マッサージ指圧師（`anma`）
- **対象問題数**: 全150問（Part 1〜6、各25問）
- **整備レイヤー**:
  - `10_questionType_fixed`
  - `15_correctChoiceText_fixed`
  - `23_correctChoiceText_fixed`
  - `21_explanationText_added`

## 2. 特記事項・医学的根拠の整理
- **問1（医療法上の医療提供施設）**: 介護老人福祉施設は医療提供施設に含まれない。
- **問3（ノーマライゼーションの理念）**: 障害者と健常者が共に通常の社会生活を送る社会理念。
- **問18（腋窩神経の支配筋）**: 三角筋および小円筋を支配。
- **問45（熱傷の9の法則）**: 成人の頭頸部9%、体幹前後各18%、上肢片側9%、下肢片側18%、会陰部1%。
- **問131（指圧の基本原則）**: 垂直の原則・持続の原則・集中の原則。

## 3. 検証結果
- `python3 scripts/check/check_questiontype_patch_coverage.py --list-group-id 2019 --base-dir output/anma/questions_json`
  - `question_2019_1.json` 〜 `question_2019_6.json`: 全て **[OK]**
- `00_source` 不変性チェック: 完全維持。
