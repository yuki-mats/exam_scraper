# 一級ボイラー技士 `kako-mon.com` source導入監査

- 実施日: 2026-09-20
- 対象資格: 一級ボイラー技士 (`boiler1`)
- 取得元: `https://kako-mon.com/bo-1/`
- 結論: 3試験回120問の`00_source`を取得し、source quality gateと公式正答全件照合に合格した。

## 取得結果

| 試験回 | 問題 | 選択肢 | 問題画像 | source SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `2025-2` | 40 | 200 | 2 | `10c9960c8af404ad462105aed88c8d0b549f4048828111cceb87f973e337c8f8` |
| `2025-1` | 40 | 200 | 1 | `1678c6a2a8846d09c7b975772e509f2b3c21a55bec1878da7359ea0f5fe12359` |
| `2024-2` | 40 | 200 | 2 | `dcd6540f42881426c466949dcb7ccbf4783e7cb19a079efd7d864fccaf9fd1c2` |

全120ページで、URL・見出しの問番号、5肢の連番、`.question-footer[data-corr]`、`.choice-correct`、`.answer-body`の正答表示を照合した。canonical keyと`source_question_id`は120件すべて一意である。

## 公式正答との照合

安全衛生技術試験協会の次の公表問題PDFから正答記号を抽出し、各40問の正答番号を`00_source`と順番どおり全件比較した。

| source試験回 | 公式PDF | 照合結果 |
| --- | --- | --- |
| `2025-2` | [LC20260402-1.pdf](https://www.exam.or.jp/wp-content/uploads/2026/04/LC20260402-1.pdf) | 40 / 40一致 |
| `2025-1` | [LC20252102.pdf](https://www.exam.or.jp/wp-content/uploads/2025/10/LC20252102.pdf) | 40 / 40一致 |
| `2024-2` | [LC20251102.pdf](https://www.exam.or.jp/wp-content/uploads/2025/04/LC20251102.pdf) | 40 / 40一致 |

## 目視確認

取得元の実ページで問題文、5肢、正答表示を確認した。画像を含む全5問は、ブラウザ表示とローカル取得画像も見比べた。

| 試験回 | 問番号 | 正答 | 目視対象 |
| --- | ---: | ---: | --- |
| `2025-2` | 3 | 5 | 応力・ひずみ線図、問題文、5肢、正答表示、取得GIF |
| `2025-2` | 9 | 3 | 図、問題文、5肢、正答表示、取得GIF |
| `2025-1` | 10 | 1 | 図、問題文、5肢、正答表示、取得GIF |
| `2024-2` | 12 | 2 | 図、問題文、5肢、正答表示、取得GIF |
| `2024-2` | 25 | 3 | 図、問題文、5肢、正答表示、取得GIF |

3冊の公式PDFもページ画像として開き、表紙・問題配置・正答記号を目視した。機械抽出だけを根拠にはしていない。

## 機械検査

- `tests.test_scrape_kakomon`、`tests.test_scrape_presets`及び共通化対象の既存scraper回帰テストに合格した。
- `tools/question_bank/question_bank.py quality-gate --mode source`は3試験回すべて合格した。
- `docs/contracts/00_source_sha256_manifest.jsonl`へ3ファイルのhashを登録した。
