# 柔道整復師 補助ドキュメント

このディレクトリには、柔道整復師国家試験に固有の試験構造、カテゴリ設計資料、法令スコープを置きます。解説文の書き方は全資格共通の`prompt/03_prompt_add_explanationText.md`を使います。

## 対象データ

- 資格コード: `judoseifukushi`
- 取得元: `output/judoseifukushi/questions_json`
- 対象年: 1993〜2026年
- 対象問題数: 7,600問
- category: `output/judoseifukushi/category/category.json`

## 資料

- `01_exam_profile.md`: 年度構成、科目、問題形式
- `03_category_preparation.md`: `category.json`を設計又は見直す場合の資料
- `04_law_reference_policy.md`: 関係法規・制度問題の法令スコープ

工程03は`00_source`の問題・選択肢・解説候補を材料にします。解説候補が欠損又は根拠不足なら、法令はe-Gov、行政資料は所管官庁、資格制度は試験実施団体などの公式一次資料で確認します。
