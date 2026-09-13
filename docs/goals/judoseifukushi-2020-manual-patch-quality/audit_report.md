# 柔道整復師 2020年（第28回）全250問 目視整備 最終監査報告書

## 1. 監査概要
- **対象年度**: 2020年（第28回）
- **対象資格**: 柔道整復師（judoseifukushi）
- **総問題数**: 250問（午前125問、午後125問／全10ファイル）
- **実施方針**: 機械的一括置換スクリプトを厳禁とし、1問ずつ問題文・全選択肢・公式解答を目視精査してパッチを作成・検証。
- **監査日**: 2026-09-13
- **監査結果**: **合格（全検査完全通過）**

## 2. 成果物一覧
全10ファイル（`question_2020_1.json` 〜 `question_2020_10.json`）について以下の各パッチ層を作成・配置：
- `10_questionType_fixed`: 設問の形式（`true_false`, `flash_card`, `group_choice`）および計算問題フラグ（`isCalculationQuestion: True/False`、問87の肺胞換気量計算問題等を含む）の適合化
- `15_correctChoiceText_fixed`: 正答意図（`select_correct`, `select_incorrect`）の確定
- `18_law_context_prepared`: 関係法規設問のフラグ・条文コンテキスト整備
- `21_explanationText_added`: 文化庁基準に準拠した文頭結論先行（「正しい。」「間違い。」）、自然な主語（法令名・条文名を文頭主語にしない）、重複表現排除による高品質解説
- `22_questionSetId_linked`: 年度識別子（`judoseifukushi_2020`）の紐付け
- `23_correctChoiceText_fixed`: 公式正答に完全整合した選択肢正誤配列および修正メタデータ（複数正答の問109、問154、問216、問239等の適正反映）

## 3. 検証コマンドと監査結果
以下の公式チェッカーを全10ファイル（計40回）実行し、全件ノーエラーで合格：
1. `scripts/check/check_questiontype_patch_coverage.py` -> PASS (10/10)
2. `scripts/check/check_question_intent_patch_coverage.py` -> PASS (10/10)
3. `scripts/check/check_correct_choice_patch_coverage.py --require-full --require-change-meta` -> PASS (10/10)
4. `scripts/check/check_explanation_patch_coverage.py --correct-choice-patch ...` -> PASS (10/10)
5. `scripts/check/check_00_source_immutability.py --check-staged` -> PASS (`00_source` の完全不可変性を維持)

## 4. 特記事項・注目問題の対応
- **複数正答問題の適切な判定**:
  - 問109: ブルンストロームステージ回復過程の反射（対側性連合反応、レミスト反応の2つが正解）
  - 問154: 失調性歩行の症状（足もとを目で確かめながら歩く、動揺しながら歩くの2つが正解）
  - 問216: 関節内骨折（バートン骨折、ショウファー骨折の2つが正解）
  - 問239: 手根骨舟状骨骨折の特徴（受傷直後X線で確認困難、骨癒合遷延の2つが正解）
  これらは `questionType: "flash_card"` とし、該当肢を「正しい」として適切な解説を作成した。
- **計算問題の明示**:
  - 問87: 肺胞換気量の計算問題において `isCalculationQuestion: True` を設定。
- **否定設問における正誤判定の統一**:
  - 「〜でないのはどれか」「〜誤っているのはどれか」「〜低いのはどれか」等の断片肢否定設問について、正解選択肢（誤った内容の肢）を「間違い」、それ以外の肢を「正しい」とし、解説冒頭の結論と完全一致させた。

## 5. 総評
2020年の全250問について、医学・解剖学・生理学・病理学・臨床医学・柔道整復理論および関係法規の専門知識に基づき、誤解のない明確で学習価値の高い解説と正確な正答データを整備完了した。
