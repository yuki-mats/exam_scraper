# 鍼灸師 補助ドキュメント

このディレクトリには、鍼灸師過去問に固有の試験構造、カテゴリ設計資料、法令スコープを置きます。西洋医学、東洋医学、症例を含む解説文の書き方は、全資格共通の`prompt/03_prompt_add_explanationText.md`を使います。

## 対象データ

- 資格コード: `shinkyu`
- 取得元: `output/shinkyu/questions_json`
- 対象年: 1993〜2026年
- 対象問題数: 5,560問
- category: `output/shinkyu/category/category.json`

## 資料

- `01_exam_profile.md`: 年度構成、科目、問題形式
- `03_category_preparation.md`: `category.json`を設計又は見直す場合の資料
- `04_law_reference_policy.md`: 関係法規・免許・業務範囲の法令スコープ

## 解説の資格固有調整

- 西洋医学では構造・機能・病態・所見・治療の対応、東洋医学では陰陽・五行・気血津液・臓腑・経絡経穴・証の対応のうち、正誤を分ける関係を示す。
- 施術問題は、方法や効果だけでなく、禁忌、感染対策、熱傷や気胸などの安全上の条件が判断を分ける場合に明示する。
