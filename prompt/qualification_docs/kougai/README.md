# 公害防止管理者 補助ドキュメント

このディレクトリには、公害防止管理者試験に固有の取得データ構造とカテゴリ設計資料を置きます。解説文の書き方は全資格共通の`prompt/03_prompt_add_explanationText.md`を使います。

## 資格固有の前提

- canonical sourceはyaku-tikである。
- 取得範囲は2010〜2025年で、`questionLabel`と`source_question_id`のprefixが安定している。
- 取得済み問題は全問`true_false`だが、穴埋め由来の設問が多い。
- `category.json`はJEMAI公式の18試験科目をfolder、公式PDF「試験科目の範囲」のnumbered rangeをquestionSetとする。

## 資料

- `01_exam_profile.md`: 取得範囲、科目、問題形式
- `03_category_preparation.md`: `category.json`を設計又は見直す場合の資料

## 解説の資格固有調整

- 穴埋め由来の問題は正しい語句を直接示す。処理設備・処理法は、対象となる物質、処理原理、適用範囲のうち正誤を分ける要素を結び付ける。
