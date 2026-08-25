# T004 blocker evidence

## 判定

- 建築画像7点はremote URLから2回取得でき、内容量、Content-Type、ETag、SHA-256が一致した。画像本体はrepositoryへ保存せず、runごとにOS一時領域へ取得し、承認済みhashと一致する場合だけ使用できる。
- 柔道整復師の法令5問のうち2024年・2025年の3問は、e-Gov法令履歴と厚生労働省一次資料により、試験時点と現行時点を分けて固定できる見込みである。
- 1993年・2002年の2問は、e-Gov APIの過去本文対応範囲外であり、試験時点のraw本文を再現可能な一次根拠として固定できない。Lawzillaもこの環境では資格情報がなく、確定根拠に使えなかった。
- 旧2問はoracleを見ず、問題文、選択肢、年、出題意図だけで選んだ2020年の同種2問へ差し替えるのが安全である。

## 画像固定値

| 問題ID | role | Content-Type | bytes | SHA-256 |
|---|---|---:|---:|---|
| `64bd269e44533561` | question | image/webp | 28948 | `695ae47a9fce6004f6fc5e805d4cbfc2c2a542dab3e6842d2c700e3c23a5c4c1` |
| `64bd269e44533561` | choice1 | image/webp | 3330 | `6cfe12a310488493764e316c05359a7d0ee266ab410e1795394e2d7a0f9ede26` |
| `64bd269e44533561` | choice2 | image/webp | 3264 | `7101db4f4e0312cedfa2895f7b8af13feae66645d3a9b04bacbdd2c6dd91811f` |
| `64bd269e44533561` | choice3 | image/webp | 3242 | `79142016649d3cebec9316ffacfab51ef5b3561159329b06a0e05cfda909bc12` |
| `64bd269e44533561` | choice4 | image/webp | 3464 | `177dc50726d4ec2279e6b4afe6720b4bb09fbc8efa4581895e8ea34a5924bf02` |
| `64bd269e44533561` | choice5 | image/webp | 3562 | `1f99d78b1b891ecbdf17764550def62a99d60eda595087026150749fc1fea380` |
| `ed7d14b661421a12` | question | image/png | 6720 | `689d469d7bdc3f20968c8f35ae8f3d9cc262c47727224a3c02ddbafa73077765` |

## 法令証拠

- 柔道整復師法の2024-03-03/2025-03-02時点revision: `345AC1000000019_20220617_504AC0000000068`、raw JSON SHA-256 `1a6d9f878f04f424bef1f382bf7f5614a8b71c9ccc893fb57cc3ec16dc524b02`。
- 同法の2026-08-25時点revision: `345AC1000000019_20250601_504AC0000000068`、raw JSON SHA-256 `bd279b1054704589bd60af19ce300e9eb0e82a567d8d65f5e91ed80dfc9897c5`。
- 柔道整復師法施行規則の2024-03-03/2025-03-02/2026-08-25時点revision: `402M50000100020_20220728_504M60000100107`、raw JSON SHA-256 `0cd6b47a3e01745574086b9985f8ef3082b3d3b0d2746bce676898d1a84e1a3a`。
- `c5167b46942fb08e`は受領委任取扱規程と明細書交付通知、`0eb595c2c11278f5`は法第6条・第15条と施行規則第4条、`8987ec55216cbc63`は法第24条と厚生労働大臣指定事項告示を問別に固定する必要がある。

## 差替え候補

- `dfb3fe84e07f47f9` → `d732ddbaf0d4f522`（2020年、広告できない事項、`question_2020_2.json#question_bodies[21]`）
- `1ebaca9b85c6dd6e` → `ef0992b6887ec00b`（2020年、施術所構造設備、`question_2020_2.json#question_bodies[20]`）

候補IDは問題文、選択肢、年、出題意図のみで固定し、選定時に正答・oracleを参照していない。
