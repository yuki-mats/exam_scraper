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

## JEMAI 公式試験科目

- 公害総論
- 大気概論
- 大気特論
- ばいじん・粉じん特論
- 大気有害物質特論
- 大規模大気特論
- 水質概論
- 汚水処理特論
- 水質有害物質特論
- 大規模水質特論
- 騒音・振動概論
- 騒音・振動特論
- ばいじん・一般粉じん特論
- ダイオキシン類概論
- ダイオキシン類特論
- 大気・水質概論
- 大気関係技術特論
- 水質関係技術特論

## 現行 source との関係

- 2010〜2025 の yaku-tik canonical source は、上記18科目のうち主に大気・水質系10科目を含む。
- `category.json` は source に存在する科目だけでなく、JEMAI 公式18科目を folder として保持する。
- questionSet は PDF「試験科目の範囲」の各 numbered range に対応するため、yaku-tik の prefix と 1:1 ではない。

## 01〜04 への示唆

- 01 `questionType`: 全問 true_false なので、設問文と選択肢構造が true_false として成立しているかを見る。
- 02 `questionIntent`: 「正しいものを選ぶ」のか「誤っているものを選ぶ」のかを設問末尾で確定する。
- 03 `explanationText`: 各 choice の正誤を短く明示し、空欄の語句を対応づける。
- 04 `questionSetId`: 年度や yaku-tik prefix ではなく、JEMAI 公式PDFの範囲に最も近い `kougai_qs<folder番号>_<範囲番号>` を使う。
