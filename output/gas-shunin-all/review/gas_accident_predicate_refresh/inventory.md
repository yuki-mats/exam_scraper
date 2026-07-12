# ガス事故問題の棚卸し

## 対象

- 資格: ガス主任技術者（甲種・乙種）
- 抽出条件: `40_convert/*.json` が存在し、問題文がガス事故又は事故報告を扱う問題
- 棚卸し件数: 16問（甲種7問、乙種9問）
- 判定方法: 問題文と各選択肢を結合して完全な判定命題を作り、その命題に対する `correctChoiceText` と基本解説の向きを確認

## 結果

- 整合済み・変更なし: 12問
- 解説更新パッチ適用・Firestore再公開・readback済み: 4問
- `00_source` の変更: なし

| 種別 | 年 | 問 | sourceQuestionKey | 棚卸し結果 |
| --- | ---: | ---: | --- | --- |
| 甲種 | 2019 | 2 | `gas-shunin:kou:2019:law:q02` | 整合済み・変更なし |
| 甲種 | 2020 | 3 | `gas-shunin:kou:2020:law:q03` | 整合済み・変更なし |
| 甲種 | 2021 | 4 | `gas-shunin:kou:2021:law:q04` | 整合済み・変更なし |
| 甲種 | 2022 | 4 | `gas-shunin:kou:2022:law:q04` | 整合済み・変更なし |
| 甲種 | 2023 | 4 | `gas-shunin:kou:2023:law:q04` | 整合済み・変更なし |
| 甲種 | 2024 | 4 | `gas-shunin:kou:2024:law:q04` | 整合済み・変更なし |
| 甲種 | 2025 | 4 | `gas-shunin:kou:2025:law:q04` | 整合済み・変更なし |
| 乙種 | 2017 | 2 | `gas-shunin:otsu:2017:law:q02` | 整合済み・変更なし |
| 乙種 | 2018 | 2 | `gas-shunin:otsu:2018:law:q02` | 整合済み・変更なし |
| 乙種 | 2019 | 2 | `gas-shunin:otsu:2019:law:q02` | 整合済み・変更なし |
| 乙種 | 2020 | 3 | `gas-shunin:otsu:2020:law:q03` | 更新済み・Firestore公開確認済み |
| 乙種 | 2021 | 4 | `gas-shunin:otsu:2021:law:q04` | 更新済み・Firestore公開確認済み |
| 乙種 | 2022 | 4 | `gas-shunin:otsu:2022:law:q04` | 整合済み・変更なし |
| 乙種 | 2023 | 4 | `gas-shunin:otsu:2023:law:q04` | 更新済み・Firestore公開確認済み |
| 乙種 | 2024 | 4 | `gas-shunin:otsu:2024:law:q04` | 更新済み・Firestore公開確認済み |
| 乙種 | 2025 | 4 | `gas-shunin:otsu:2025:law:q04` | 整合済み・変更なし |

## 更新内容

### 乙種 2020年 問3

- 判定命題: 各事故は「ガス事故速報を報告することが規定されていない事故」である。
- 更新後の `correctChoiceText`: `間違い / 正しい / 間違い / 正しい / 間違い`
- 更新理由: 既存値は速報対象である事故を「正しい」としており、問題文の否定条件と正誤方向が逆だった。
- パッチ: `output/gas-shunin-otsu/questions_json/2020/21_explanationText_added/question_2020_1_explanationText_added.json`

### 乙種 2021年 問4

- 判定命題: 各事故は「ガス事故速報を報告することが規定されていない事故」である。
- 更新後の `correctChoiceText`: `間違い / 間違い / 間違い / 正しい / 間違い`
- 更新理由: 既存値は速報対象である事故を「正しい」としており、問題文の否定条件と正誤方向が逆だった。
- パッチ: `output/gas-shunin-otsu/questions_json/2021/21_explanationText_added/question_2021_1_explanationText_added.json`

### 乙種 2023年 問4

- 判定命題: 各事故は「ガス事故速報を報告することが規定されていない事故」である。
- 更新後の `correctChoiceText`: `間違い / 間違い / 正しい / 間違い / 間違い`
- 更新理由: 既存値は速報対象である事故を「正しい」としており、問題文の否定条件と正誤方向が逆だった。
- パッチ: `output/gas-shunin-otsu/questions_json/2023/21_explanationText_added/question_2023_1_explanationText_added.json`

### 乙種 2024年 問4

- 判定命題: 各事故は「ガス事故速報を報告することが規定されていない事故」である。
- 更新後の `correctChoiceText`: `間違い / 正しい / 間違い / 間違い / 正しい`
- 更新理由: `21_explanationText_added` の正誤値は整合していたが、原本・前段正誤パッチから公開成果物へmergeすると選択肢3・5が旧方向に戻る状態だった。後段正誤パッチでロ・ホを正しい組合せとして固定し、問題文の否定条件との対応が分かる解説へ更新した。
- パッチ: `output/gas-shunin-otsu/questions_json/2024/21_explanationText_added/question_2024_1_explanationText_added.json`

## 解説の記述方針

各選択肢の解説は、次の順序で完結させる。

1. 根拠となる表・条文が、その事故を速報又は詳報の対象としているかを示す。
2. 問題文が「規定されていない事故」を選ぶ問題であることを示す。
3. 復元した判定命題に対して、その選択肢が正しいか間違いかを明示する。

組合せ問題の正解番号だけから選択肢単体の正誤を推定せず、問題文と選択肢を結合した判定命題を基準にする。
