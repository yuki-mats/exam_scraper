# 2018年（第26回）あん摩マッサージ指圧師国家試験 4層パッチ整備・監査レポート

## 1. 概要
- **年度**: 2018年（第26回）
- **対象資格**: あん摩マッサージ指圧師（`anma`）
- **対象問題数**: 全150問（Part 1〜6、各25問）
- **整備レイヤー**:
  - `10_questionType_fixed`
  - `15_correctChoiceText_fixed`
  - `23_correctChoiceText_fixed`
  - `21_explanationText_added`

## 2. 特記事項・医学的根拠の整理
- **問1（介護保険制度の要介護認定申請先）**: 被保険者の居住する市町村・特別区に対して申請。
- **問3（リスボン宣言と患者の権利）**: 自己決定権とインフォームド・コンセントが基軸。
- **問19（下肢帯の支配神経）**: 大殿筋は仙骨神経叢由来の下殿神経支配。
- **問39（インスリンの血糖降下作用）**: 膵ランゲルハンス島B細胞（β細胞）から分泌。
- **問131（指圧療法の3大原則）**: 垂直の原則・持続の原則・集中の原則。

## 3. 検証結果
- `python3 scripts/check/check_questiontype_patch_coverage.py --list-group-id 2018 --base-dir output/anma/questions_json`
  - `question_2018_1.json` 〜 `question_2018_6.json`: 全て **[OK]**
- `00_source` 不変性チェック: 完全維持。
