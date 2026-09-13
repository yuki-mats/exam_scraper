# 柔道整復師 2022年（第30回）全250問 目視精査・パッチ品質監査報告書

## 1. 監査概要

- **対象資格**: 柔道整復師（judoseifukushi）
- **対象年度**: 2022年（第30回国家試験）全250問（問1〜問250）
- **対象ファイル**: `question_2022_1.json` 〜 `question_2022_10.json`（全10ファイル）
- **監査担当**: Judge / PM / Worker
- **実施日**: 2026年9月13日
- **方針**:
  - 機械的一括スクリプト処理を厳禁とし、全250問の問題文・全選択肢・公式解答を1問ずつ目視精査。
  - 文化庁「公用文作成の考え方」および参考書水準の自然な日本語解説（文頭結論先行、自然な主語、法令名を主語にしない）。
  - 断片肢否定設問における命題補完統一（`questionIntent: select_correct`、補完命題が真となる肢に「正しい」）。
  - 完全文誤り選択設問における論理的一貫性（`questionIntent: select_incorrect`、誤りの肢に「間違い」）。
  - 複数正答問題および「2つ選べ」設問（問132, 137, 143, 148, 150, 155, 172, 185, 233等）における `true_false` 形式と公式解答の完全整合。
  - 単一 `main` ブランチ運用（`origin/main` 直接コミット・プッシュ）。
  - `00_source` の完全保護・不変性維持。

---

## 2. 成果物配置（正本ワークフロー準拠）

`config/question_maintenance_workflow.toml` の定義に完全準拠し、以下のディレクトリに全10ファイル（計40パッチ）を配置：

1. **問題形式パッチ（`10_questionType_fixed/`）**:
   - `question_2022_1_questionType_fixed.json` 〜 `question_2022_10_questionType_fixed.json`（全10ファイル・250問）
   - 全問 `isCalculationQuestion: false`、適切な `flash_card` / `true_false` 判定を付与。
2. **設問意図パッチ（`15_correctChoiceText_fixed/`）**:
   - `question_2022_1_correctChoiceText_fixed.json` 〜 `question_2022_10_correctChoiceText_fixed.json`（全10ファイル・250問）
   - 完全文の誤り選択設問は `select_incorrect`、それ以外（断片肢否定設問を含む）は `select_correct` へ統一。
3. **正答精査パッチ（`23_correctChoiceText_fixed/`）**:
   - `question_2022_1_correctChoiceText_fixed.json` 〜 `question_2022_10_correctChoiceText_fixed.json`（全10ファイル・250問）
   - 各選択肢の正誤判定（「正しい」/「間違い」）を公式解答と完全照合。変更メタデータ（`correctChoiceText_changed`, `correctChoiceText_change_detail`, `correctChoiceText_change_reason`）も完備。
4. **解説パッチ（`21_explanationText_added/`）**:
   - `question_2022_1_explanationText_added.json` 〜 `question_2022_10_explanationText_added.json`（全10ファイル・250問）
   - 全選択肢に「正しい。」「間違い。」を先頭に冠し、医学的根拠・関係法令条文を明快かつ平易に解説。関係法規設問（柔道整復師法、医療法等）には `isLawRelated: true`, `isLawGrounded: true` を付与。

---

## 3. 検証結果

全10ファイル（250問）に対して、公式チェッカー4種を実行し、全件100%合格を確認済み：

| ファイル | 問題番号 | 形式チェック | 意図チェック | 正答チェック (--require-full --require-change-meta) | 解説チェック (--require-law-grounded-flag) | 判定 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `question_2022_1.json` | 問1〜問25 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_2.json` | 問26〜問50 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_3.json` | 問51〜問75 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_4.json` | 問76〜問100 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_5.json` | 問101〜問125 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_6.json` | 問126〜問150 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_7.json` | 問151〜問175 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_8.json` | 問176〜問200 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_9.json` | 問201〜問225 | PASS | PASS | PASS | PASS | 合格 |
| `question_2022_10.json` | 問226〜問250 | PASS | PASS | PASS | PASS | 合格 |

- **不変性検証**: `judoseifukushi` 配下の `00_source`（2022年度の全10ファイル含む）について、`00_source` 親ディレクトリ移動・改変 0 件を確認済み。

---

## 4. 結論

柔道整復師 2022年（第30回）全250問の目視精査およびパッチ作成・検証は、すべての受入基準（Oracle条件・品質原則・Git運用規約）を満たして完了した。
