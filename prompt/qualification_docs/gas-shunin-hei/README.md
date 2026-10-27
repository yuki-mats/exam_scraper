# ガス主任技術者丙種の整備方針

対象は2017〜2025年の公式マークシート問題、各年度58問である。取得元HTMLは取得時点の記録として保持し、問題文・全選択肢・公式正答の根拠には同年度丙種のJIA公式PDFを使う。

## 公式資料

- 公式問題・解答一覧: <https://www.jia-page.or.jp/exam/examination/answer/>
- 取得済みPDFとURL・hash: `document/sources/gas-shunin/official_exam_pdf_catalog.json`
- 問題PDF: `output/pdf/gas-shunin-official/<year>/q_hei_*.pdf`
- 解答PDF: `output/pdf/gas-shunin-official/<year>/a_hei_*.pdf`
- 問題のページ画像: `output/gas-shunin-hei/pdf_review/<year>/mark_sheet_question/page-XX.png`
- 解答のページ画像: `output/gas-shunin-hei/pdf_review/<year>/mark_sheet_answer/page-01.png`
- 全522問のPDF照合記録: `document/temporary/20261027_gas_shunin_hei_pdf_review.json`
- 本文・共通注の修正と独立監査: `document/temporary/20261027_gas_shunin_hei_pdf_patches.json`

PDFの科目と問番号を毎問照合する。法令16問、基礎理論15問、ガス技術27問（製造1〜9、供給10〜18、消費機器19〜27）を、年度・科目・問番号で識別する。別科目や別年度の同じ番号を結び付けない。本文の正式修正は24のパッチにあり、後工程は修正済みの現在projectionを読む。`00_source`は編集しない。

gassyunin.comの本文、judge欄、解答表示を正誤や解説の根拠にしない。同サイトを再閲覧しない。現存する解答labelと公式正答の一致を毎問確かめ、記述の真偽と選択問題で選ぶ答えを区別する。集約回答型では公式の全記述・組合せと公式正答から各記述の真偽を確定し、一意に決まらなければ保留する。

## 解説・法令

解説は共通の`prompt/03_prompt_add_explanationText.md`に従う。計算問題は前提、式、単位、数値代入と結論が読み手に追えるようにする。図を文章化した問題は、24に保存された図の条件と各選択肢を説明に反映する。

法令は[法令根拠方針](01_law_reference_policy.md)を参照する。出題当時の正誤と現行法を混同せず、現行法差分は通常の03bと解説工程で審査する。技術問題を、その設備が法規制されるという理由だけで法令問題へ変えない。

## 分類

[分類設計](03_category_preparation.md)に従う。乙種の本番分類は参考資料であり、乙種の問題・正答・解説・IDを丙種へコピーしない。
