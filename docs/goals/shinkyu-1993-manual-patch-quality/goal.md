# 目標: 1993年（第1回）はり師きゅう師国家試験 4層パッチ手動整備と品質検証

- 対象年度: 1993年（第1回）
- 対象資格: はり師きゅう師（`shinkyu`）
- 総問題数: 160問（全7ファイル）

## 整備方針
1. 単一選択式問題として `10_questionType_fixed` (`questionType: "true_false"`) を整備。
2. 問題文主旨に基づく `15_correctChoiceText_fixed` (`questionIntent`) を整備。
3. 厚生労働省公式正答に基づく `23_correctChoiceText_fixed` (`correctChoiceText`: "正しい"/"間違い") を整備。
4. 文化庁「公用文作成の考え方」に準拠した `21_explanationText_added` （文頭「正しい。」「間違い。」統一）を整備。
5. 1ファイルごとに公式チェッカー検証、Gitコミット・`origin/main` へのプッシュを実施。
