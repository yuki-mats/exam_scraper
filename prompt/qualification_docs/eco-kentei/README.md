# eco検定（環境社会検定試験）

- ローカル資格コード: `eco-kentei`
- 取得元の扱い・画像取得・文字起こし: [Knoun取得契約](../../../document/sources/knoun/source_contract.md)
- 取得済み入力の構造と05の参照資料: [試験・入力概要](01_exam_profile.md)
- 試験範囲の参照先: [東京商工会議所の試験要項](https://kentei.tokyo-cci.or.jp/eco/exam-info/)

ユーザーの指定により全問を05独自問題化へ送る。原画像の取得、転記照合、`00_source`登録を済ませてから、[共通の独自問題ワークフロー](../../../document/operations/original_question_authoring_workflow.md)へ進める。

取得元の民間模擬問題の印字正答は、公式試験の公式正答と区別する。現行法で作成した独自問題の03bは、[field契約の作成時基準](../../../document/reference/question_field_contract.md#lawrevisionfacts-契約)に従い、実際の05作成記録とその日の一次本文を用いて監査する。取得元の年版、画像取得日又は法改正年から、公式試験日を推測しない。

問題文・正答・解説の事実確認は、文字の転記照合とは別に行う。取得元解説を確認済みの法令・政策・統計として扱わず、後続工程で現行の一次根拠に照らす。

## 取得元の表記差異と作問時の確認

UNESCOの名称は「国際連合教育科学文化機関」であり、「国連教育科学文化機関」とも表記する。[文部科学省「ユネスコとは」](https://www.mext.go.jp/unesco/003/001.htm)と[外務省の概要](https://www.mofa.go.jp/mofaj/gaiko/culture/kyoryoku/unesco/gaiyo.html)で名称、本部所在地、活動目的を確認できる。

模擬問題4の`mondai4_26`は、選択肢と解説本文では「国連教育科学文化機関」、正答見出しでは「国際教育科学文化機関」と印字されている。原画像を対で照合した差異としてsourceに双方の原文を残す。05以降は上の一次資料で確認した名称を使い、取得元の見出しの表記差を独自問題へ引き継がない。

SDGsの目標3では、医薬品・ワクチンの研究開発支援を含むターゲット3.bと、UHC・必須医薬品などへのアクセスを扱う3.8を区別する。模擬問題5の`mondai5_27`の独自解説は、[外務省の2030アジェンダ仮訳](https://www.mofa.go.jp/mofaj/gaiko/oda/files/000101402.pdf)で設問に直接対応するターゲットを確認し、取得元の3.8の説明だけで研究開発支援の根拠としない。

環境表示の図を作る場合は、[農林水産省の有機JAS制度](https://www.maff.go.jp/j/jas/jas_kikaku/yuuki.html)、[MSCの認証説明](https://www.msc.org/jp/what-we-are-doing/the-blue-msc-label-what-it-means-for-you-jp)、[資源エネルギー庁の省エネラベル説明](https://www.enecho.meti.go.jp/category/saving_and_new/saving/enterprise/retail/touitsu_shoenelabel/)を確認する。目安電気料金を表示する例では、[家電公取協の目安単価](https://www.eftc.or.jp/qa/?post=1126)と採用した単価を照合し、取得元の古い表示例を現在の一律料金として説明しない。

国際機関の名称だけでなく、その組織上の位置付けも確認する。UNEPは[外務省の「国連計画・基金」](https://www.mofa.go.jp/mofaj/fp/unp_a/page22_001294.html)に整理されており、国連の専門機関と同一視しない。IUCNの委員会名は[IUCN日本委員会の一覧](https://www.iucn.jp/program/about_iucn/commission/)に合わせ、`mondai1_27`の独自本文でも「教育・コミュニケーション委員会（CEC）」の名称を使う。

`mondai1_9`では、省エネ基準への適合義務、建築確認中の審査、省エネ適判を区別する。[国土交通省の説明](https://www.mlit.go.jp/report/press/content/001762396.pdf)と建築物省エネ法第10条・第11条を確認し、2025年4月以降に着工する新築住宅・非住宅への原則適合義務を、例外なく同じ審査を受ける義務へ広げない。

`mondai1_4`のパリ協定は、[環境省が公表する協定和訳](https://www.env.go.jp/content/900444088.pdf)の第4条（緩和）と第7条（適応）を確認する。取得元の解説だけを根拠に、適応策がパリ協定で初めて扱われたと断定しない。条約本文の取得・配送は共通の公式資料取得契約を使い、架空のe-Gov法令IDを作らない。

`mondai1_70`のバーゼル条約では、第6条の事前通告・同意、第8条の再輸入、第9条の不法取引を区別する。不法取引の原因者や代替措置の条件を確認し、すべての場合に輸出者が無条件で返送すると説明しない。[環境省の2016年合同会議資料](https://www.env.go.jp/press/103133.html)に掲載された関係資料集にも条約の抜粋があり、共通の公式資料取得契約では条約全文と区別して取得する。資料集の取得日は条約の改正日を意味せず、現在及び出題時点への適用性は別に監査する。

`mondai1_45`では、[気象庁のオゾン層の説明](https://www.jma.go.jp/jma/kishou/know/env/ozonehp/3-10ozone.html)と[EIAのオゾンの温室効果の説明](https://www.eia.gov/tools/faqs/faq.php?id=84&t=11)を別々に確認する。成層圏オゾンによる有害紫外線の吸収と、オゾン自体の温室効果は両立する。05では紫外線の吸収を否定する明確な誤肢へ整え、温室効果を持つこと自体を誤りの理由にしない。CFC等と全てのフロンを混同せず、回復傾向は[WMOの科学的観測](https://wmo.int/media/news/wmo-bulletin-shows-successes-and-challenges-ozone-layer-recovery)で確認し、法律の目的条文だけから観測結果を導かない。

`mondai1_59`では、ITS全体の定義と、その技術で支援できる行為を分ける。[国土交通省のITSの説明](https://www.mlit.go.jp/road/ITS/j-html/)と[ETC2.0の説明](https://www.mlit.go.jp/road/ITS/j-html/etc2/index.html)を参照し、料金収受や運転支援が可能であること自体を否定しない。05で誤答候補をロードプライシングという料金制度、エコドライブという運転方法そのものの定義として明確にし、単に「できる」とある原文を別の定義へ読み替えて正誤を固定しない。

`mondai1_81`では、放射性物質汚染対処特措法第25条の地域指定、第28条の実施計画、第30条の国による実施を区別する。原文の「不法投棄にも施設確保の課題がある」という趣旨を、原正答番号から偽と断定しない。[豊島の不法投棄対策で整備した施設](https://www.pref.kagawa.lg.jp/junkan/teshima/shiryo/shi-15.html)にも保管・処理施設の実例がある。独自問題では、[災害廃棄物等の処理](https://shiteihaiki.env.go.jp/radiological_contaminated_waste/regional_measures/flow_of_processing.html)と汚染廃棄物に共通する用地確保の必要性を、各肢の文面から独立に判定できる比較へ整える。

`mondai3_85`のメタン濃度の順位は、[気象庁の温室効果ガスの区分](https://www.jma.go.jp/jma/kishou/know/faq/faq6.html)に沿って、人間活動によって増加した主なガスという範囲を明記し、水蒸気を含む全ての温室効果ガスへ広げない。本文にある2022年の数値は過去の値として扱い、現在の濃度と呼ばない。

森林面積の割合は、統計年と区分を明示して問う。[FAOのFRA2020](https://www.fao.org/interactive/forest-resources-assessment/2020/en/)では、2020年の世界の森林面積のうち熱帯の気候帯に属する森林は45％であり、熱帯多雨林だけの割合ではない。また、2010～2020年の世界の森林面積の年平均純減は470万haで、伐採・転用による森林減少の総面積とは区別する。いずれも2020年までの統計であり、作成時点の最新値とは呼ばない。

`mondai4_31`の取得元の正答「40」は時点・区分・丸め方が明示されていないため、sourceにはそのまま残す。05でFRA2020の2020年値を問う形に明確化する場合は、上記の区分に合わせて当該数値候補を45へ修正し、その問の全候補、正誤及び正答表示を改めて確定する。原文の「40」と新しい問いの「45」を混在させたり、他の問へ正答番号を流用したりしない。

`mondai4_42`では、[環境省のLCAの概要](https://www.env.go.jp/earth/ondanka/lca/)で、製品・サービスのライフサイクルを通じた環境影響を評価する手法という定義を確認する。供給連鎖の合理化・効率化を図るSCMは、[国土交通省の説明](https://www.mlit.go.jp/seisakutokatsu/soukou/ppg/ppg6/ppg6-06.htm)と区別する。自動車の適用例では、[スズキのLCA説明](https://www.suzuki.co.jp/about/csr/lca/)と[トヨタのMIRAIのLCA報告書](https://global.toyota/pages/global_toyota/sustainability/esg/challenge2050/challenge2/life_cycle_assessment_report_en.pdf)を確認し、走行段階のCO₂排出と製造等を含む全体評価を分ける。05では、LCAを燃費改善の効果やハイブリッド車等の比較に活用できるという、確認できた適用例へ整える。「LCA分析の結果からハイブリッド車が開発された」という歴史的因果関係を、これらの資料だけで断定しない。取得元の記述と参考の「約80％」はsourceに保持し、車種・走行距離等の条件がない一律の現在値として独自解説へ転記しない。

`mondai4_43`では、[環境影響評価法](https://laws.e-gov.go.jp/law/409AC0000000081)第2条と[同法施行令](https://laws.e-gov.go.jp/law/409CO0000000346)第1条・別表第1で、対象となる事業の種類・規模と国の関与に関する要件を分けて確認する。交付金を用いることだけで全事業が対象になるとはせず、対象港湾計画も法第47条・第48条の条件に沿って説明する。法第62条は条例との関係を定める。SEAは[2007年の環境省ガイドライン](https://www.env.go.jp/press/8247.html)があり、2011年4月に初めてその考え方が生まれたわけではない。[国土技術政策総合研究所の説明](https://www.nilim.go.jp/lab/dcg/kadai1-asses.html)で、2011年4月27日の改正法公布と2013年4月1日の完全施行を区別する。05では④を、2011年の改正法が事業の位置・規模等の検討段階における配慮書手続を設けたことを問う記述へ明確化し、政策や上位計画のすべてを法定評価の対象にしたと広げない。取得元の記述と日付はsourceに保持する。
