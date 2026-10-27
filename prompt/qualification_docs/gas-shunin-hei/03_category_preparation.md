# ガス主任技術者丙種の分類設計

## 根拠と構成

乙種の本番folder・questionSet構成を参考に、丙種の全年度の問題文と全選択肢、JIAの公式試験科目、専門団体の丙種教材構成を照合して分類する。

- JIA公式問題・正答: <https://www.jia-page.or.jp/exam/examination/answer/>
- 日本コミュニティーガス協会の丙種教材一覧: <https://www.jcga-page.or.jp/seminar/distribution/>
- 乙種分類: `output/gas-shunin-otsu/category/category.json`
- 本番乙種の参照snapshot: `output/gas-shunin-hei/production/20261027/before-folders-raw.json`、`before-questionSets-raw.json`

専門団体の教材は基礎、製造、供給、消費機器、法令を分けている。乙種の構成を参考に、法令は事業法規と設備の技術基準へ分け、基礎理論、製造、供給、消費機器と合わせて6 folderを基本とする。法令の2 folderは公式上位分類「法令」、製造・供給・消費機器の3 folderは「ガス技術」に対応する。`metadata.officialCategoryMappings`へ対応を記録する。

folderの表示名は科目・学習分野とし、甲乙丙の区分を含めない。`qualificationId=gas-shunin-hei`で丙種を区別する。本番の丙種専用categoryは新規であり、乙種のB系IDを共有しない。materialized IDとcanonical IDは共有taxonomy方針に従う。

## 単元の境界

乙種の小分類は、論点の候補として参照する。丙種に出題のない都市ガスの大規模製造設備等をそのまま全件複製せず、9年度の反復論点から復習単元を設ける。名称だけでなく、各questionSetのdescriptionに含む論点と隣接単元との違いを定義する。

- 法令の事業区分、定義、義務、手続、資格、保安体制、周知・調査は事業法規へ置く。技術省令等が規定する設備の性能・構造・設置基準は設備の技術基準へ置く。技術的な保安知識だけを問う問題は法令へ寄せない。
- 基礎理論は物性、熱、化学、燃焼、流体、伝熱、材料等の復習単元を区別する。製造設備で用いる計算でも、設問が一般理論を問う場合は基礎理論を検討する。
- 製造はLPGの性質・原料、容器・貯槽、気化・供給能力、製造設備の運用と防災を扱う。需要家までの導管・整圧・メーター等は供給、燃焼機器の使用・給排気・安全装置等は消費機器で扱う。
- 接続具、ガス栓、警報器、一酸化炭素中毒等は、設問が復習を求める機能・利用場面と全選択肢を読んで確定する。単語一つや元科目だけで割り当てない。
- 複数の小論点を明示的に比較させる問題に必要な場合だけ、その比較範囲をdescriptionで定めた複合単元を設ける。分類に迷う問題を受け入れる無制限の「その他」は作らない。

03cでcategoryを確定してschema-only dry-runを通し、04はその固定categoryに存在するquestionSetIdだけへ一問ずつ紐付ける。分類の不足・境界の矛盾は03cへ戻す。
