# 公害防止管理者 補助ドキュメント

このディレクトリには、公害防止管理者試験に固有の取得データ構造とカテゴリ設計資料を置きます。問題形式判定は`prompt/01_prompt_fix_questionType.md`、設問意図判定は`prompt/02_prompt_add_questionIntent.md`、解説文の書き方は`prompt/03_prompt_add_explanationText.md`を使います。

## 資格固有の前提

- canonical sourceはyaku-tikである。
- 取得範囲は2010〜2025年で、`questionLabel`と`source_question_id`のprefixが安定している。
- 出題形式には、各選択肢が独立した命題となる正誤問題（`true_false`）、数値計算や単一事項の導出問題（`flash_card`）、下線部選択・複数空欄の組合せ・順序配列問題（`group_choice`）が含まれるため、工程01で一問ずつ構造に即して分類する。
- `category.json`はJEMAI公式の18試験科目をfolder、公式PDF「試験科目の範囲」のnumbered rangeをquestionSetとする。

## 資料

- `01_exam_profile.md`: 取得範囲、科目、問題形式・単元設計の基準
- `03_category_preparation.md`: `category.json`を設計又は見直す場合の資料

## 解説の資格固有調整

- 穴埋め・下線部由来の問題は正しい語句・条文・数値を直接示す。処理設備・処理法・測定法は、対象となる物質、処理・測定原理、適用範囲のうち正誤を分ける要素を具体的に結び付ける。
