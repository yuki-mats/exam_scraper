import json
import os
from scripts.common.question_identity import review_question_id

def create_patches_for_part(part_num, answers_data):
    source_file = f"output/anma/questions_json/1994/00_source/question_1994_{part_num}.json"
    with open(source_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    bodies = data.get("question_bodies", [])

    patch_10 = []
    patch_15 = []
    patch_23 = []
    patch_21 = []

    for i, item in enumerate(answers_data):
        idx, corr, qtype, is_calc, expl = item
        rqid = review_question_id(bodies[i])
        patch_10.append({
            "original_question_id": rqid,
            "questionType": "true_false",
            "isCalculationQuestion": is_calc
        })
        patch_15.append({
            "original_question_id": rqid,
            "correctChoiceText": corr
        })
        patch_23.append({
            "original_question_id": rqid,
            "correctChoiceText": corr
        })
        patch_21.append({
            "original_question_id": rqid,
            "explanationText": expl
        })

    base_dir = "output/anma/questions_json/1994"
    os.makedirs(f"{base_dir}/10_questionType_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/15_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/23_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/21_explanationText_added", exist_ok=True)

    with open(f"{base_dir}/10_questionType_fixed/question_1994_{part_num}_questionType_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_10, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/15_correctChoiceText_fixed/question_1994_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_15, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/23_correctChoiceText_fixed/question_1994_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_23, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/21_explanationText_added/question_1994_{part_num}_explanationText_added.json", "w", encoding="utf-8") as f:
        json.dump(patch_21, f, ensure_ascii=False, indent=2)

    print(f"Generated all 4 layers of patches for question_1994_{part_num}.json successfully!")


part1_answers = [
    (1, "結核", "select_correct", False,
     "1950年（昭和25年）当時の日本の主要死因第1位は「結核」でした。その後、抗結核薬の普及や衛生環境の改善により結核死亡率は激減し、悪性新生物（がん）や心疾患、脳血管疾患などの生活習慣病が上位を占めるようになりました。"),
    (2, "健康増進", "select_correct", False,
     "予防医学における第一次予防は、発症そのものを未然に防ぐ「健康増進」「特異的予防（予防接種・環境衛生等）」です。早期発見・早期治療は第二次予防、機能訓練や社会復帰は第三次予防に分類されます。"),
    (3, "ツベルクリン反応", "select_correct", False,
     "ツベルクリン反応検査は結核菌に対する細胞性免疫（遅延型過敏反応）を調べる検査であり、結核の診断や感染既往・BCG接種効果の判定に用いられます。"),
    (4, "麻疹", "select_correct", False,
     "麻疹（はしか）は空気感染（飛沫核感染）により感染する伝染性の強いウイルス感染症です。破傷風は創傷感染、日本脳炎は蚊による媒介、ポリオは主に経口感染（糞口感染）です。"),
    (5, "細菌", "select_correct", False,
     "コレラはコレラ菌（Vibrio cholerae）という「細菌」の経口感染によって引き起こされる急性消化器感染症です。"),
    (6, "死体解剖保存法", "select_correct", False,
     "解剖学教育のための正常解剖や病理解剖、死因究明のための行政解剖・承諾解剖を規定している法律は「死体解剖保存法」です。"),
    (7, "70", "select_correct", False,
     "消毒用エタノールは「約70〜80v/v％（通常70％）」の水溶液において蛋白質凝固作用と浸透性が最もバランスよく働き、最大の殺菌効果を発揮します。"),
    (8, "煮沸消毒法", "select_correct", False,
     "煮沸消毒法は熱湯（100℃）の物理的作用を利用する「理学的（物理的）消毒法」です。逆性石けん、生石灰、昇こう（昇汞）水は化学物質を用いる「化学的消毒法」です。"),
    (9, "精神遅滞 ─ 神経症", "select_incorrect", False,
     "精神遅滞（知的障害）は発達期における知的機能の発達障害であり、心理的葛藤やストレス等によって生じる「神経症（不安神経症・心因性障害等）」とは病態が異なります。"),
    (10, "放射線 ─ ルクス", "select_incorrect", False,
     "ルクス（lux）は「照度」の単位です。放射線の単位としては、放射能を表すベクレル（Bq）、吸収線量を表すグレイ（Gy）、生体影響・等価線量を表すシーベルト（Sv）などが用いられます。"),
    (11, "不快指数 ─ 気圧", "select_incorrect", False,
     "不快指数（DI）は「気温（乾球温度）」と「湿度（湿球温度）」から算出される体感温度の指標であり、気圧は計算要素に含まれません。"),
    (12, ["素行が著しく不良であれば相対的欠格事由となる。", "本籍、氏名の変更をした場合は 30 日以内に知事に申請しなければならない。"], "select_incorrect", False,
     "法改正により素行不良は欠格事由から削除されており、また本籍・氏名の変更時の登録事項変更申請は厚生労働大臣（指定登録機関）に対して30日以内に行うため、選択肢2および3の双方が誤り（正解）となります。"),
    (13, "免許記載事項の変更は都道府県知事に届出る。", "select_incorrect", False,
     "あん摩マッサージ指圧師免許は国家資格（厚生大臣免許）であるため、免許証の書換交付申請や登録事項の変更は「厚生大臣（厚生労働大臣）」に対して行います。都道府県知事への届出とする記述は誤りです。"),
    (14, "成人病の治療", "select_incorrect", False,
     "保健所は地域保健法に基づき、地域の公衆衛生・予防衛生活動（水質検査、感染症対策、栄養指導、保健指導等）を統括する行政機関であり、一般的な医療機関のような「成人病（生活習慣病）の治療」は業務に含まれません。"),
    (15, "環境衛生 － 自治省", "select_incorrect", False,
     "公衆衛生および環境衛生に関する行政の所管官庁は「厚生省（現・厚生労働省および環境省）」です。自治省（現・総務省）ではありません。"),
    (16, "血管内皮 － 移行上皮", "select_incorrect", False,
     "血管内皮は「単層扁平上皮」で構成されます。移行上皮（尿路上皮）は腎盂、尿管、膀胱などの尿路粘膜に特有の上皮組織です。"),
    (17, "翼状突起", "select_correct", False,
     "蝶形骨の翼状突起は頭蓋底の深部に位置し、体表から直接触れることはできません。肩峰、肘頭、頸切痕（胸骨柄上縁）はいずれも体表から容易に触知できる骨性ランドマークです。"),
    (18, "顎舌骨筋", "select_correct", False,
     "舌骨上筋群には顎二腹筋、茎突舌骨筋、顎舌骨筋、オトガイ舌骨筋の4筋が含まれます。肩甲舌骨筋、胸骨甲状筋、甲状舌骨筋、胸骨舌骨筋は舌骨下筋群です。"),
    (19, "胸管は大静脈孔を通る。", "select_incorrect", False,
     "横隔膜の3大裂孔において、胸管および下行大動脈は「大動脈裂孔（第12胸椎の高さ）」を通過します。大静脈孔（第8胸椎の高さ）を通るのは下大静脈です。"),
    (20, "大殿筋", "select_incorrect", False,
     "大殿筋は腸骨翼外面や仙骨後面から起こり、主に大腿骨の「臀筋粗面」および「腸脛靭帯」に停止します。大転子に停止するのは中殿筋、小殿筋、梨状筋などです。"),
    (21, "屈曲 － 短掌筋", "select_incorrect", False,
     "短掌筋は手掌腱膜から手掌の皮膚に付く皮筋であり、手掌の皮膚を緊張させる作用を持ちます。手関節の屈曲（掌屈）には橈側手根屈筋、尺側手根屈筋、長掌筋などが働きます。"),
    (22, "側頭骨", "select_correct", False,
     "副鼻腔（上顎洞、前頭洞、篩骨洞、蝶形骨洞）を形成する骨は上顎骨、前頭骨、篩骨、蝶形骨の4骨です。側頭骨には副鼻腔は存在しません。"),
    (23, "肝静脈は肝門を通る。", "select_incorrect", False,
     "肝門を出入するのは「固有肝動脈」「門脈」「肝管（胆管系）」および神経・リンパ管です。肝静脈は肝臓の後上面から直接「下大静脈」に注ぎ込むため、肝門は通りません。"),
    (24, "半月ヒダ", "select_correct", False,
     "「半月ヒダ（結腸半月ヒダ）」および結腸ヒモ、腹膜垂は大腸（結腸）に特有の構造です。胃には大網、小弯、噴門腺、胃小窩などが存在します。"),
    (25, "ボーマン嚢", "select_correct", False,
     "腎小体（マルピーギ小体）は、毛細血管網である「糸球体」とそれを包む「ボーマン嚢（ボウマン嚢）」から構成されます。")
]

part2_answers = [
    (26, "髄質からはアドレナリンが分泌される。", "select_correct", False,
     "副腎髄質は交感神経節後線維に相当するクロム親和性細胞からなり、カテコールアミンである「アドレナリン」やノルアドレナリンを血中に分泌します。副腎皮質からはステロイドホルモン（コルチゾール、アルドステロン等）が分泌されます。"),
    (27, "胸腺", "select_correct", False,
     "「胸腺」は前縦隔（胸骨の後方で心臓の前上方）に位置する一次リンパ組織（免疫器官）です。上皮小体および甲状腺は頸部にあり、松果体は間脳背側にあります。"),
    (28, "左心室", "select_correct", False,
     "心室壁の筋層の厚さは、全身へ高い血圧で血液を送り出す「左心室」が最も厚く、右心室の約3倍の厚みを有します。"),
    (29, "腎臓", "select_correct", False,
     "腹腔動脈の3終枝（胃左動脈、総肝動脈、脾動脈）は胃、肝臓、胆嚢、膵臓、脾臓、十二指腸を栄養します。腎臓は腹大動脈から直接分岐する「腎動脈」によって栄養されます。"),
    (30, "椎骨動脈", "select_correct", False,
     "脳への動脈供給は「内頸動脈」と「椎骨動脈（脳底動脈を経て大脳動脈輪を形成）」の2系統によって担われます。外頸動脈、顔面動脈、後頭動脈は頭頸部の外側組織を栄養します。"),
    (31, "右上半身", "select_correct", False,
     "全身のリンパのうち「右上半身（右頭頸部・右上肢・右胸部）」のリンパは右リンパ本幹に集まり右静脈角へ注ぎます。左半身および両側の下半身のリンパはすべて「胸管」に注ぎます。"),
    (32, "外転神経", "select_correct", False,
     "「外転神経（第Ⅵ脳神経）」は外側直筋のみを支配する純運動性神経です。迷走神経は混合神経、内耳神経は純感覚性神経、三叉神経は混合神経です。"),
    (33, "腰神経叢 － 坐骨神経", "select_incorrect", False,
     "坐骨神経は「仙骨神経叢（L4〜S3）」から起始する人体最大の神経です。腰神経叢（T12〜L4）の主要な枝は大腿神経と閉鎖神経です。"),
    (34, "大脳基底核", "select_correct", False,
     "痛みの体性感覚伝導路は、脊髄後角（第1次ニューロン）→ 外側脊髄視床路 → 視床（第3次ニューロン）→ 大脳皮質体性感覚野（中心後回）へと至ります。「大脳基底核」は錐体外路系の運動制御中枢であり、痛覚伝導の直接中枢ではありません。"),
    (35, "毛は真皮の変形したものである。", "select_incorrect", False,
     "毛、爪、皮脂腺、汗腺などの皮膚付属器は「表皮（外胚葉）」が真皮内に陥入・増殖・角化して形成された変形物です。真皮由来ではありません。"),
    (36, "左側は腕頭動脈から分枝する。", "select_incorrect", False,
     "大動脈弓からの分枝順序において、右総頸動脈は腕頭動脈から分岐しますが、「左総頸動脈」は大動脈弓から直接単独で分岐します。"),
    (37, "肩甲骨", "select_correct", False,
     "後頸三角（外側頸三角）は、前縁を「胸鎖乳突筋後縁」、後縁を「僧帽筋前縁」、下底を「鎖骨中央1/3」が形成する三角形の間隙です。肩甲骨は直接の境界を構成しません。"),
    (38, "副神経", "select_correct", False,
     "「副神経（第Ⅺ脳神経）」は頭蓋腔から頸静脈孔を通って頸部（胸鎖乳突筋・僧帽筋）に分布し、胸腔（胸郭上口）内には入りません。横隔神経、食道、気管、総頸動脈、鎖骨下動脈などは胸郭上口を通過します。"),
    (39, "中心体を含む。", "select_incorrect", False,
     "中心体は細胞分裂時の紡錘体形成に関与する細胞小器官であり、「細胞質」に存在します。細胞核内にはDNA（遺伝子）、クロマチン（染色体）、核小体が存在します。"),
    (40, "リンパ球", "select_correct", False,
     "白血球のうち「リンパ球（特にBリンパ球から分化した形質細胞）」が免疫グロブリン（抗体）を産生・分泌します。"),
    (41, "炭水化物である。", "select_incorrect", False,
     "ヘモグロビン（血色素）はグロビンという蛋白質にヘム（鉄含有ポルフィリン錯体）が結合した「複合蛋白質（含金蛋白質）」です。炭水化物（糖質）ではありません。"),
    (42, "血管平滑筋の弛緩", "select_correct", False,
     "細動脈などの血管平滑筋が弛緩すると、末梢血管抵抗が減少し血管断面積が拡大するため「血圧が低下（下降）」します。血液量増加や血管収縮、血液粘度上昇は血圧を上昇させます。"),
    (43, "重炭酸ナトリウム", "select_correct", False,
     "胃から送られてくる強酸性の胃液（塩酸）混じりの粥状液は、膵液に含まれる多量の「重炭酸ナトリウム（重炭酸塩：NaHCO3）」によって中和され、十二指腸粘膜を保護するとともに至適pHを弱アルカリ性に保ちます。"),
    (44, "蛋白質", "select_correct", False,
     "筋線維（筋細胞）の細胞質（原形質・筋形質）および筋原線維（アクチン・ミオシン等）の主成分をなす栄養素は「蛋白質」です。"),
    (45, "蒸発", "select_correct", False,
     "外気温が皮膚温（約30〜33℃）を超えると、放射・伝導・対流による熱放散は不能となり、発汗による「水分蒸発の潜熱（気化熱）」が唯一かつ急激に増加する主要な放熱機序となります。"),
    (46, "クレアチニン", "select_correct", False,
     "「クレアチニン」は糸球体でろ過された後、尿細管でほとんど再吸収されずに全量が尿中へ排泄されるため、腎機能（糸球体ろ過量：GFR）の指標として用いられます。"),
    (47, "性ホルモン", "select_correct", False,
     "コレステロールを原料とするステロイドホルモンには、副腎皮質ホルモン（コルチゾール、アルドステロン）および「性ホルモン（エストロゲン、プロゲステロン、テストステロン等）」が含まれます。下垂体ホルモンはペプチド・蛋白質ホルモンです。"),
    (48, "パラソルモン", "select_correct", False,
     "上皮小体（副甲状腺）から分泌される「パラソルモン（PTH）」は、骨吸収の促進、腎尿細管でのカルシウム再吸収促進、ビタミンD活性化を介して血中カルシウム濃度を上昇させます。"),
    (49, "乳汁の産生", "select_correct", False,
     "下垂体前葉から分泌される「プロラクチン（黄体刺激ホルモン・催乳ホルモン）」は、乳腺の発育および「乳汁の産生・分泌維持」をつかさどります。"),
    (50, "樹状突起から伝達物質が放出される。", "select_incorrect", False,
     "シナプス伝達において、化学伝達物質（アセチルコリン等）がシナプス小胞から開口放出行われるのは「軸索末端（シナプス前膜）」です。樹状突起や細胞体（シナプス後膜）には受容体が存在します。")
]

if __name__ == "__main__":
    create_patches_for_part(1, part1_answers)
    create_patches_for_part(2, part2_answers)
