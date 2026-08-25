# T007 official law evidence

## 公式試験日

| 回 | 実施日 | 厚生労働省URL | raw bytes | SHA-256 |
|---|---|---|---:|---|
| 第28回 | 2020-03-01 | `https://www.mhlw.go.jp/general/sikaku/successlist/2020/siken16/about.html` | 8624 | `a66f6a99824adf9e23a6d071f1504059b5c2ece82861344a1ca82a5a3a159c0b` |
| 第32回 | 2024-03-03 | `https://www.mhlw.go.jp/general/sikaku/successlist/2024/siken16/about.html` | 8137 | `481c22128e0d89bd52e9883dc23108c17212d1edda832eb069e67318cbe8b2e7` |
| 第33回 | 2025-03-02 | `https://www.mhlw.go.jp/general/sikaku/successlist/2025/siken16/about.html` | 8599 | `bc45105525f3c7b6300e67a930005dc892ab1d47e793100179b72adde9cce953` |

各ページ本文の「○年○月○日（日）に実施した標記試験」をlocatorとし、HTTP bodyの生バイト全体をhash対象にする。

## e-Gov時点別本文

API request:

`https://laws.e-gov.go.jp/api/2/law_data/{law_id}?asof={YYYY-MM-DD}&response_format=json&law_full_text_format=json&json_format=full`

### 柔道整復師法 `345AC1000000019`

| as-of | revision | raw bytes | SHA-256 |
|---|---|---:|---|
| 2020-03-01 | `345AC1000000019_20160401_426AC0000000069` | 151827 | `9720d7c32e521da4c78947dfb66c312906a0d61ed51f21ce6ff8e235f4347aaa` |
| 2024-03-03 | `345AC1000000019_20220617_504AC0000000068` | 143462 | `1a6d9f878f04f424bef1f382bf7f5614a8b71c9ccc893fb57cc3ec16dc524b02` |
| 2025-03-02 | `345AC1000000019_20220617_504AC0000000068` | 143462 | `1a6d9f878f04f424bef1f382bf7f5614a8b71c9ccc893fb57cc3ec16dc524b02` |
| 2026-08-25 | `345AC1000000019_20250601_504AC0000000068` | 143470 | `bd279b1054704589bd60af19ce300e9eb0e82a567d8d65f5e91ed80dfc9897c5` |

### 柔道整復師法施行規則 `402M50000100020`

| as-of | revision | raw bytes | SHA-256 |
|---|---|---:|---|
| 2020-03-01 | `402M50000100020_20190507_501M60000100001` | 58775 | `877cdaeecab2b0e330dd1ce5dcc567ac82ae249166c5788caa42df6cb705ffe6` |
| 2024-03-03 | `402M50000100020_20220728_504M60000100107` | 63071 | `0cd6b47a3e01745574086b9985f8ef3082b3d3b0d2746bce676898d1a84e1a3a` |
| 2025-03-02 | `402M50000100020_20220728_504M60000100107` | 63071 | `0cd6b47a3e01745574086b9985f8ef3082b3d3b0d2746bce676898d1a84e1a3a` |
| 2026-08-25 | `402M50000100020_20220728_504M60000100107` | 63071 | `0cd6b47a3e01745574086b9985f8ef3082b3d3b0d2746bce676898d1a84e1a3a` |

## 厚生労働省の通知・告示

- 2024受領委任: `https://www.mhlw.go.jp/bunya/iryouhoken/iryouhoken13/dl/220530_01.pdf`、令和4年5月27日 保発0527第2号、975598 bytes、SHA-256 `b210a18cc1262761e375c8d43e170ef7115767b5e7df418c50b7b546d63c693c`。PDF page 4、別添第3章20。
- current受領委任: `https://www.mhlw.go.jp/bunya/iryouhoken/iryouhoken13/dl/240610_03.pdf`、現在配信本文は最終改正令和8年6月1日 保発0601第5号、226416 bytes、SHA-256 `c279e7f47e8c7827bbbb6c24202d063c80ca45de23ad0265459541dbbb3d0d5e`。page 5/9/10/11。
- 広告指定告示: `https://www.mhlw.go.jp/web/t_doc?dataId=80999361&dataType=0&pageNo=1`、平成11年3月29日 厚生省告示第70号、4982 bytes、SHA-256 `8468f5dc5db3a5f6a542ff5ba7b9f3361ce2870faafbbfc03f63f58032d6f497`。`#l000000005`〜`#l000000012`。
- 広告改正通知: `https://www.mhlw.go.jp/web/t_doc?dataId=00tc2055&dataType=1&pageNo=1`、平成28年告示第272号／医政発0629第3号、6690 bytes、SHA-256 `a0349e8dba25bac1d71798b3f102fba2f7bbc652bddbe7dd1eb99ae616ee2e55`。

## 問別locator

- `c5167b46942fb08e`: 受領委任取扱規程、第3章20「領収証及び明細書の交付」。
- `0eb595c2c11278f5`: 柔道整復師法第6条第1・2項、第15条、施行規則第4条第1〜3項。
- `8987ec55216cbc63`: 柔道整復師法第24条第1項各号・第2項、告示第70号、平成28年告示第272号。
- `4ef67113801362d9`: 柔道整復師法第17条第1項。
- `ef0992b6887ec00b`: 柔道整復師法施行規則第18条第1項第1〜4号。

HTTPリダイレクト後のbodyを加工せず保存してSHA-256を計算する。e-Gov APIもas-of付きresponse全体をhash対象にする。
