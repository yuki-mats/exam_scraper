# ガス主任技術者試験 公式PDFアーカイブ

JIA（日本ガス機器検査協会）が公開したガス主任技術者試験の問題・論述問題・正答PDFを、再調査せず参照できるように保管します。

## 正本

- PDF保存先: `output/pdf/gas-shunin-official/<西暦>/`
- URL・SHA-256・ページ数・容量: [`official_exam_pdf_catalog.json`](official_exam_pdf_catalog.json)
- 取得・復元・検証: [`archive_gas_shunin_official_pdfs.py`](../../../scripts/scrape/archive_gas_shunin_official_pdfs.py)
- JIA公式一覧: [試験の問題と解答](https://www.jia-page.or.jp/exam/examination/answer/)

対象は、JIA公式URL又はそのInternet Archive保存版を確認できる2017年（平成29年度）から2025年（令和7年度）までです。各年度について、甲種・乙種・丙種のマークシート問題と正答、甲種・乙種共通の論述問題、丙種の論述問題を保存します。

## 利用順序

1. `official_exam_pdf_catalog.json`から年度、種別、資料種別に合う`localPath`を選ぶ。
2. ローカルPDFを一次資料として読む。問題の照合ではPDFページと冊子上のページ、科目、問番号を記録する。
3. PDFが欠ける場合だけ、次のコマンドでcatalogに固定済みのURLから復元する。

```bash
.venv/bin/python scripts/scrape/archive_gas_shunin_official_pdfs.py sync
```

通常の確認ではWeb検索をやり直しません。JIAの掲載年度が増えた場合だけ、取得可能性を確認してcatalogを更新します。

## 検証

```bash
.venv/bin/python scripts/scrape/archive_gas_shunin_official_pdfs.py verify
```

全PDFについて、PDF header、暗号化の有無、ページ数、容量、SHA-256をcatalogと照合します。問題・正答の内容修正には、この検証を通過したPDFだけを使います。
