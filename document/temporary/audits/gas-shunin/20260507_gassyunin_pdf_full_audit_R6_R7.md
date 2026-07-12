更新日: 2026-05-07

# `gassyunin.com` と原本PDFの全問照合（R6/R7）

目的: 令和6・令和7の全問題について、`gassyunin.com` の `各選択肢の判定` セクションから得られる選択肢記号・順序（`judgeChoiceMarkers`）が、原本PDFの設問における選択肢記号・順序と一致するかをページ画像を1枚ずつ目視で確認する。

前提:
- R7(令和7年度乙種): `/Users/yuki/Downloads/q_otsu_R7.pdf` → 画像: `scratch/pdf_R7/page_??.png`
- R6(令和6年度乙種): `/Users/yuki/Downloads/q_otsu_r6.pdf` → 画像: `scratch/pdf_R6/page_??.png`
- `gassyunin` 抽出一覧:
  - `scratch/gassyunin_markers_2025.json`
  - `scratch/gassyunin_markers_2024.json`

判定基準:
- PDF上の選択肢記号が `(1)〜(5)` の場合: `judgeChoiceMarkers` が `["1","2","3","4","5"]` であること
- PDF上の記述が `イ/ロ/ハ/ニ/ホ` の場合: `judgeChoiceMarkers` が `["イ","ロ","ハ","ニ","ホ"]` で、並びが一致すること
- `judgeChoiceMarkers` が空の場合: `gassyunin` 側に `各選択肢の判定` が存在しない（または判定ブロックが提供されない）タイプとして扱い、選択肢正本を `judge` に一本化できない候補として記録する

注意:
- 全116問を「原本PDF vs gassyuninサイト」を完全自動で突合するにはOCRが必要だが、この環境にはOCR基盤（tesseract/easyocr等）が無い。
- 代替として、`gassyunin` 側で `judgeChoiceMarkers` が取得できた問題は「判定セクションが存在し、選択肢記号が抽出できた」ことの証拠とし、PDF側はページ画像を目視で確認して記号種別・順序が一致するかを検証する。

## R7 (2025)

- `scratch/pdf_R7/page_01.png`: 表紙（問題なし）
- `scratch/pdf_R7/page_02.png`: 区分説明（問題なし）
- `scratch/pdf_R7/page_03.png`: (法)問1 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_04.png`: (法)問2 `イロハニホ` -> OK
- `scratch/pdf_R7/page_05.png`: (法)問3 `イロハニホ` -> OK
- `scratch/pdf_R7/page_06.png`: (法)問4 `イロハニホ` -> OK
- `scratch/pdf_R7/page_07.png`: (法)問5 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_08.png`: (法)問6 `イロハニホ` -> OK
- `scratch/pdf_R7/page_09.png`: (法)問7 `イロハニホ` -> OK
- `scratch/pdf_R7/page_10.png`: (法)問8 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_11.png`: (法)問9 `イロハニホ` -> OK
- `scratch/pdf_R7/page_12.png`: (法)問10 `イロハニホ` -> OK
- `scratch/pdf_R7/page_13.png`: (法)問11 `イロハニホ` -> OK
- `scratch/pdf_R7/page_14.png`: (法)問12 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_15.png`: (法)問13 `イロハニホ` -> OK / (法)問14 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_16.png`: (法)問15 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_17.png`: (法)問16 `イロハニホ` -> OK
- `scratch/pdf_R7/page_18.png`: (基)問1 `(1)〜(5)` -> OK / (基)問2 `(1)〜(5)` -> `judgeChoiceMarkers`空（判定セクション依存は不可）
- `scratch/pdf_R7/page_19.png`: (基)問3 `(1)〜(5)` -> `judgeChoiceMarkers`空（判定セクション依存は不可）/ (基)問4 `(1)〜(5)` -> `judgeChoiceMarkers`空（判定セクション依存は不可）
- `scratch/pdf_R7/page_20.png`: (基)問5 `(1)〜(5)` -> OK / (基)問6 `(1)〜(5)` -> OK / (基)問7 `(1)〜(5)` -> `judgeChoiceMarkers`空（判定セクション依存は不可）
- `scratch/pdf_R7/page_18.png`: (基)問1 `(1)〜(5)` -> OK / (基)問2 `(1)〜(5)` -> `judgeChoiceMarkers` 空（要別経路）
- `scratch/pdf_R7/page_16.png`: (法)問15 `(1)〜(5)` -> OK
- `scratch/pdf_R7/page_17.png`: (法)問16 `イロハニホ` -> OK

### R7総括（記号・並び）

`scratch/gassyunin_markers_2025.json` の `judgeChoiceMarkers` 有無で分類すると、R7の58問は次の状態。

- `judgeChoiceMarkers` が取得できた: 47問
- `judgeChoiceMarkers` が空（=判定セクションから選択肢を取れない）: 11問

カテゴリ別の `judgeChoiceMarkers` 空の内訳（=「各選択肢の判定」正本一本化ができない候補）:

- 基礎理論: 問2, 問3, 問4, 問7, 問8, 問10, 問11, 問12, 問15（9問）
- 供給: 問10, 問13（2問）

法令/製造/消費機器は `judgeChoiceMarkers` 空の問題は無かった。

## R6 (2024)

（未記入）
