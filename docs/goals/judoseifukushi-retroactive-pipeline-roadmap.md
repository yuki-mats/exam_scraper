# 柔道整復師 過去問目視整備 遡及実行ロードマップ（2023年〜2013年）

## 1. 概要と基本方針

本ロードマップは、柔道整復師国家試験の未整備年度（2023年〜2013年、計11年度・2,610問）について、受験生にとって利用価値の高い**直近年度から過去へ順次遡りながら、1問ずつ目視精査によって高品質に整備を完遂する**ための統括実行台帳です。

### 基本原則
1. **機械的一括自動置換の厳禁**: スクリプトによる一括置換や形式的自動生成は禁止し、全問の問題文・全選択肢・公式解答を1問ずつ目視精査する。
2. **文化庁「公用文作成の考え方」準拠の高品質解説**:
   - **文頭結論先行**: 全選択肢の解説冒頭を「正しい。」「間違い。」で始める。
   - **自然な主語**: 人・物・解剖用語・疾患名を主語に置き、法令名や資料名を主語にしない。
   - **冗長表現の排除**: 「〜について」「〜において」「〜である点が誤り」などの反復を避け、動詞中心に具体的に記述する。
3. **設問意図の論理整合**:
   - 断片肢否定設問（「〜でないのはどれか」等）は `questionIntent: select_correct` を適用。
   - 完全文誤り設問は `select_incorrect` を適用。
4. **4種の公式チェッカーによる機械検証**:
   - `check_questiontype_patch_coverage.py`（形式10）
   - `check_question_intent_patch_coverage.py`（意図15）
   - `check_correct_choice_patch_coverage.py --require-full`（正答23）
   - `check_explanation_patch_coverage.py`（解説21）
5. **Git運用規約**: 単一 `main` ブランチ運用、ファイル単位での着実な検証・コミット・プッシュ、`00_source` の絶対不可変性保護。

---

## 2. 年度別進行管理台帳（直近年度から過去へ）

| 優先度 | 年度 | 試験回 | 問題数 | ゴール台帳 | 棚卸し台帳 | 状態 |
| :---: | :---: | :---: | :---: | :--- | :--- | :---: |
| **第1段** | **2023年** | 第31回 | 250問 | [`judoseifukushi-2023-manual-patch-quality/state.yaml`](judoseifukushi-2023-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2023-manual-patch-quality/question_inventory.md) | **進行中**（問1〜25完了／残225問） |
| **第2段** | **2022年** | 第30回 | 250問 | [`judoseifukushi-2022-manual-patch-quality/state.yaml`](judoseifukushi-2022-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2022-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第3段** | **2021年** | 第29回 | 250問 | [`judoseifukushi-2021-manual-patch-quality/state.yaml`](judoseifukushi-2021-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2021-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第4段** | **2020年** | 第28回 | 250問 | [`judoseifukushi-2020-manual-patch-quality/state.yaml`](judoseifukushi-2020-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2020-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第5段** | **2019年** | 第27回 | 230問 | [`judoseifukushi-2019-manual-patch-quality/state.yaml`](judoseifukushi-2019-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2019-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第6段** | **2018年** | 第26回 | 230問 | [`judoseifukushi-2018-manual-patch-quality/state.yaml`](judoseifukushi-2018-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2018-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第7段** | **2017年** | 第25回 | 230問 | [`judoseifukushi-2017-manual-patch-quality/state.yaml`](judoseifukushi-2017-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2017-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第8段** | **2016年** | 第24回 | 230問 | [`judoseifukushi-2016-manual-patch-quality/state.yaml`](judoseifukushi-2016-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2016-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第9段** | **2015年** | 第23回 | 230問 | [`judoseifukushi-2015-manual-patch-quality/state.yaml`](judoseifukushi-2015-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2015-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第10段** | **2014年** | 第22回 | 230問 | [`judoseifukushi-2014-manual-patch-quality/state.yaml`](judoseifukushi-2014-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2014-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |
| **第11段** | **2013年** | 第21回 | 230問 | [`judoseifukushi-2013-manual-patch-quality/state.yaml`](judoseifukushi-2013-manual-patch-quality/state.yaml) | [`question_inventory.md`](judoseifukushi-2013-manual-patch-quality/question_inventory.md) | 準備完了（未着手） |

- **合計対象**: 11年度・2,610問（完了済25問を除き、実残件 **2,585問**）
- **参考（整備済年度）**:
  - 2026年〜2024年（3年度・750問）：最新基準で目視精査・最終監査完了済み
  - 1993年〜2012年（20年度・約4,400問）：`judoseifukushi-01-04-full-pass` にて整備完了済み

---

## 3. 実行手順と連携

1. ユーザーからの実行合図を受信後、現在アクティブな **2023年 T003（問26〜問50）** から順次目視精査を実施。
2. 1ファイル（約25問）ごとに 4パッチ（10, 15, 23, 21）を作成し、公式チェッカー4種で検証した上で Git コミット・プッシュ。
3. 2023年の全問完了後、T012（最終監査）を実施し `audit_report.md` を作成して 2023年のゴールを `done` にクローズ。
4. 直ちに次の優先年度（2022年）の `state.yaml` を `in_progress` に遷移させ、同様に問1から順次目視整備を実行。
5. 2013年の最終監査完了まで中断なくリレー実行を行う。
