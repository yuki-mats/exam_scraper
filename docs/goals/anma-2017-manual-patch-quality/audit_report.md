# 2017年（第25回）あん摩マッサージ指圧師国家試験 4層パッチ整備・監査レポート

## 1. 概要
- **年度**: 2017年（第25回）
- **対象資格**: あん摩マッサージ指圧師（`anma`）
- **対象問題数**: 全150問（Part 1〜6、各25問）
- **整備レイヤー**:
  - `10_questionType_fixed`
  - `15_correctChoiceText_fixed`
  - `23_correctChoiceText_fixed`
  - `21_explanationText_added`

## 2. 特記事項・医学的根拠の整理
- **問1（公的医療保険制度）**: 国民皆保険制度は昭和36年（1961年）に達成。
- **問11（芽胞に効果のある消毒薬）**: 複数の選択肢（1, 3, 4）が公式正解として認められた設問。
- **問28（固有心筋の生理学的特徴）**: 固有心筋は不応期が長いため強縮を起こさず単収縮を行う。
- **問45（尺骨神経の解剖学的走行）**: 上腕骨内側上顆後方の尺骨神経溝で体表から触知容易。
- **問97（八会穴の腑会）**: 腑会は中脘（任脈）。

## 3. 検証結果
- `python3 scripts/check/check_questiontype_patch_coverage.py --list-group-id 2017 --base-dir output/anma/questions_json`
  - `question_2017_1.json` 〜 `question_2017_6.json`: 全て **[OK]**
- `00_source` 不変性チェック: 完全維持。
