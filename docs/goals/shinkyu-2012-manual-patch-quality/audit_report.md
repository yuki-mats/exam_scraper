# 2012年（第20回）はり師・きゅう師国家試験 目視精査・パッチ品質最終監査レポート

- **実施日時**: 2026-09-18 22:35:00
- **対象資格**: 鍼灸師（はり師・きゅう師）
- **対象年度**: 2012年（第20回）
- **総問題数**: 全160問（午前80問、午後80問）
- **総ファイル数**: 全7ファイル（`question_2012_1.json` 〜 `question_2012_7.json`）
- **監査結果**: **【完全合格（160/160問 全件適合）】**

---

## 1. 監査概要と実施内容

2012年（第20回）はり師・きゅう師国家試験の全160問について、1問ずつ問題文・全選択肢・公式解答を目視精査し、以下の4層パッチを完全に整備しました。

1. **`10_questionType_fixed`**: 問題形式（単一選択・計算問題有無等）の適正化
2. **`15_correctChoiceText_fixed`**: 設問意図（`select_correct` / `select_incorrect`）の厳格分類
   - 断片肢否定設問（「〜でないのはどれか」等）: `select_correct`
   - 文章肢否定設問（「誤っているのはどれか」「適切でないのはどれか」）: `select_incorrect`
3. **`23_correctChoiceText_fixed`**: 正答フラグ・公式正答番号の完全反映（複数正解・原題正答の精査反映）
4. **`21_explanationText_added`**: 文化庁「公用文作成の考え方」準拠の高品質解説（文頭結論「正しい。」「間違い。」、自然な主語、専門的かつ分かりやすい論理構成）

---

## 2. 特記事項・複数正解の精査反映

- **問8（午前問題）**: 水道水の水質基準について問う設問。公表正答に基づき、選択肢1（一般細菌）、選択肢3（遊離残留塩素）、選択肢4（pH値）の**3肢正解（1, 3, 4）**を正確に反映。
- **問59（午前問題）**: 骨格筋の収縮について問う設問。公表正答に基づき、選択肢1（筋小胞体からのCa2+放出）、選択肢4（アクチンとミオシンの結合）の**2肢正解（1, 4）**を正確に反映。

---

## 3. 4種公式チェッカー検証結果

全7ファイル（全160問）に対して以下の4種公式チェッカーを実行し、全項目でカバレッジ100%・エラー0件（PASS）を確認しました。

```bash
=== Checking question_2012_1.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
=== Checking question_2012_2.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
=== Checking question_2012_3.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
=== Checking question_2012_4.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
=== Checking question_2012_5.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
=== Checking question_2012_6.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
=== Checking question_2012_7.json ===
PASS: scripts/check/check_questiontype_patch_coverage.py
PASS: scripts/check/check_question_intent_patch_coverage.py
PASS: scripts/check/check_correct_choice_patch_coverage.py
PASS: scripts/check/check_explanation_patch_coverage.py
```

---

## 4. `00_source` 不変性検査

- `scripts/check/check_00_source_immutability.py` による検証を実施し、生データ（`00_source/`）が一切改変されていないことを証明済みです。
