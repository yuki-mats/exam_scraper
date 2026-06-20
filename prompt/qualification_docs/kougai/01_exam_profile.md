# 公害防止管理者 試験プロフィール

この文書は、`kougai` の 01〜04 作業の共通前提である。

## 全体像

- 対象は 2010〜2025 の yaku-tik 公害防止管理者過去問である。
- canonical source は 96 ファイル、2,160 問で固定する。
- 各年度は 6 ファイル、135 問で構成される。
- 全問 `questionType = true_false` で、`questionIntent` は `select_correct` と `select_incorrect` が混在する。

## 問題の性格

- 穴埋めが多く、語句の正誤を true/false で判定する形式が中心である。
- 1 問の中に複数の空欄や複数の選択肢があり、正誤の組合せを読む力が必要である。
- `questionLabel` と `source_question_id` の prefix が、実質的な科目分類になっている。

## 主要な topic 群

- 公害総論
- ばいじん・粉じん特論
- 大規模大気特論
- 大規模水質特論
- 汚水処理特論
- 水質概論
- 水質有害物質特論
- 大気概論
- 大気特論
- 大気有害物質特論

## 01〜04 への示唆

- 01 `questionType`: 全問 true_false なので、設問文と選択肢構造が true_false として成立しているかを見る。
- 02 `questionIntent`: 「正しいものを選ぶ」のか「誤っているものを選ぶ」のかを設問末尾で確定する。
- 03 `explanationText`: 各 choice の正誤を短く明示し、空欄の語句を対応づける。
- 04 `questionSetId`: 年度ではなく topic prefix で固定する。年度ごとに別 ID を作らない。

