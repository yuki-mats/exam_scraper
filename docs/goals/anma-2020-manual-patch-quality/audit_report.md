# 2020年（第28回）あん摩マッサージ指圧師国家試験 4層パッチ整備・監査レポート

## 1. 概要
- **年度**: 2020年（第28回）
- **対象資格**: あん摩マッサージ指圧師（`anma`）
- **対象問題数**: 全150問（Part 1〜6、各25問）
- **整備レイヤー**:
  - `10_questionType_fixed`
  - `15_correctChoiceText_fixed`
  - `23_correctChoiceText_fixed`
  - `21_explanationText_added`

## 2. 特記事項・医学的根拠の整理
- **問1（国民医療費の定義）**: 正常分娩や予防接種費用は国民医療費に含まれない。
- **問36（発痛増強物質）**: セロトニン・ヒスタミン・ロイコトリエンの複数選択肢（1, 2, 3）が正解とされた設問。
- **問45（悪性腫瘍の分化度）**: 高分化癌は正常組織構造を保ち、低分化癌は異型性が強く悪性度が高い。
- **問93（八会穴の骨会）**: 骨会は大杼（膀胱経）。
- **問131（指圧の3大原則）**: 垂直の原則・持続の原則・集中の原則。

## 3. 検証結果
- `python3 scripts/check/check_questiontype_patch_coverage.py --list-group-id 2020 --base-dir output/anma/questions_json`
  - `question_2020_1.json` 〜 `question_2020_6.json`: 全て **[OK]**
- `00_source` 不変性チェック: 完全維持。
