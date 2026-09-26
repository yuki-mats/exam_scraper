# 2016年（第24回）あん摩マッサージ指圧師国家試験 4層パッチ整備・監査レポート

## 1. 概要
- **年度**: 2016年（第24回）
- **対象資格**: あん摩マッサージ指圧師（`anma`）
- **対象問題数**: 全150問（Part 1〜6、各25問）
- **整備レイヤー**:
  - `10_questionType_fixed`
  - `15_correctChoiceText_fixed`
  - `23_correctChoiceText_fixed`
  - `21_explanationText_added`

## 2. 特記事項・医学的根拠の整理
- **問1（医療法上の医療提供施設）**: 施術所は医療法上の医療提供施設に含まれない。
- **問52（血圧測定と脈圧）**: 脈圧は収縮期血圧と拡張期血圧の差。
- **問128（『素問』上古天真論における腎気・身体発育）**: 女性で身体極まり最も充実するのは28歳（四七）。
- **問135（脳卒中片麻痺とブルンストロームステージ）**: 共同運動・集団屈曲可能で分離運動不能はステージIII。

## 3. 検証結果
- `python3 scripts/check/check_questiontype_patch_coverage.py --list-group-id 2016 --base-dir output/anma/questions_json`
  - `question_2016_1.json` 〜 `question_2016_6.json`: 全て **[OK]**
- `00_source` 不変性チェック: 完全維持。
