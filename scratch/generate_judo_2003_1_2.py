import json
import os
from scripts.common.question_identity import review_question_id

def create_patches_for_part(part_num, answers_data):
    source_file = f"output/judoseifukushi/questions_json/2003/00_source/question_2003_{part_num}.json"
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

    base_dir = "output/judoseifukushi/questions_json/2003"
    os.makedirs(f"{base_dir}/10_questionType_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/15_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/23_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/21_explanationText_added", exist_ok=True)

    with open(f"{base_dir}/10_questionType_fixed/question_2003_{part_num}_questionType_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_10, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/15_correctChoiceText_fixed/question_2003_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_15, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/23_correctChoiceText_fixed/question_2003_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_23, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/21_explanationText_added/question_2003_{part_num}_explanationText_added.json", "w", encoding="utf-8") as f:
        json.dump(patch_21, f, ensure_ascii=False, indent=2)

    print(f"Generated all 4 layers of patches for question_2003_{part_num}.json successfully!")


part1_answers = [
    (1, "リボソーム", "select_correct", False,
     "「リボソーム」はmRNAの情報をもとにアミノ酸をペプチド結合させて蛋白質を合成する細胞小器官です。"),
    (2, "弾性軟骨", "select_correct", False,
     "耳介軟骨、喉頭蓋軟骨、外耳道軟骨は弾性線維に富む「弾性軟骨」です。関節軟骨や肋軟骨は硝子軟骨、椎間円板や恥骨結合は線維軟骨です。"),
    (3, "上顎骨", "select_correct", False,
     "頭蓋骨のうち「上顎骨」は左右1対（2個）存在します。前頭骨、後頭骨、篩骨、蝶形骨、下顎骨、舌骨などは単一（1個）の骨です。"),
    (4, "長掌筋", "select_incorrect", False,
     "上腕骨内側上顆から起始する前腕屈筋群（浅層）は円回内筋、橈側手根屈筋、長掌筋、浅指屈筋、尺側手根屈筋です。長掌筋は内側上顆から起始するため、起始しないとする記述は誤りです。"),
    (5, "ヒラメ筋", "select_correct", False,
     "「ヒラメ筋」は腓骨頭・腓骨後面および脛骨ヒラメ筋線（単関節筋）から起始し、アキレス腱となって踵骨隆起に停止します。大腿骨から起始するのは腓腹筋（二関節筋）や足底筋です。"),
    (6, "小円筋", "select_correct", False,
     "「小円筋」は腋窩神経支配の回旋筋腱板（ローテーターカフ）構成筋です。大円筋は肩甲下神経、棘上筋・棘下筋は肩甲上神経支配です。"),
    (7, "縫工筋", "select_correct", False,
     "上前腸骨棘（ASIS）から起始し脛骨粗面内側（鵞足）に停止する筋は「縫工筋」です。大腿直筋は下前腸骨棘、大腿筋膜張筋は上前腸骨棘から腸脛靭帯へ向かいます。"),
    (8, "鎖骨下動脈", "select_correct", False,
     "前斜角筋と中斜角筋の間にある斜角筋隙を通過するのは「鎖骨下動脈」および腕神経叢です。鎖骨下静脈は前斜角筋の前方（前斜角筋隙）を通ります。"),
    (9, "後脛骨動脈", "select_correct", False,
     "内果後方（屈筋支帯下・足根管）を通過し体表から拍動を触知できる動脈は「後脛骨動脈」です。前脛骨動脈は足背動脈へ移行します。"),
    (10, "右心房", "select_correct", False,
     "心臓の筋層を栄養した冠状静脈洞の血液は「右心房」の下大静脈開口部付近へ直接開口・流入します。"),
    (11, "糸状乳頭 ─── 味蕾がない", "select_correct", False,
     "舌背全体に最も多く分布する「糸状乳頭」は角化傾向が強く、味覚受容器である味蕾を持ちません（機械的乳頭）。茸状乳頭・有郭乳頭・葉状乳頭には味蕾が存在します。"),
    (12, "十二指腸", "select_correct", False,
     "「十二指腸（球部を除く下行部・水平部・上行部）」や上行結腸、下行結腸、膵臓、腎臓、副腎は後腹壁に固定された腹膜後器官です。盲腸、横行結腸、肝臓は腹膜内器官です。"),
    (13, "披裂軟骨", "select_correct", False,
     "喉頭軟骨のうち一対の「披裂軟骨」が回旋・滑走運動することにより、声帯ヒダ（声帯靭帯）が開閉して声門裂の幅が変化します。"),
    (14, "上顎洞", "select_correct", False,
     "副鼻腔の中で最も容積が大きいのは上顎骨体内部に位置する「上顎洞（ハイモア洞）」です。中鼻道に開口します。"),
    (15, "近位曲尿細管", "select_correct", False,
     "腎小体（ボーマン嚢）から原尿が最初に出て接続する部位は「近位曲尿細管（近位尿細管）」です。続いてヘンレ係程、遠位曲尿細管、集合管へと流れます。"),
    (16, "尿管壁の筋層は平滑筋からなる。", "select_correct", False,
     "尿管壁は粘膜（移行上皮）、筋層（平滑筋）、外膜からなり、平滑筋の蠕動運動により尿を膀胱へ送ります。尿管は左右各1本（計2本）が膀胱底の後外側（膀胱三角上角）に開口します。"),
    (17, "陰核 ─── 海綿体", "select_correct", False,
     "陰核（クリトリス）は男性の陰茎海綿体に相当する勃起組織である「陰核海綿体」から構成されます。子宮壁は平滑筋、精巣上体は精巣上体管、精索は精管や血管・神経からなります。"),
    (18, "性成熟期では発育中の卵胞がみられる。", "select_correct", False,
     "性成熟期の女性の卵巣皮質内には、原始卵胞、一次卵胞、二次卵胞、成熟卵胞（グラーフ卵胞）、黄体、白体などの様々な発育段階の卵胞が存在します。"),
    (19, "顆粒を持つ。", "select_correct", False,
     "インスリンを分泌する膵島B細胞（β細胞）は、細胞質内にインスリンを含有する分泌顆粒を持ち、膵島細胞全体の約70％を占め、主に中心部に位置します。"),
    (20, "下垂体", "select_correct", False,
     "「下垂体」は口窩外胚葉（ラトケ嚢）由来の内分泌組織である腺性下垂体（前葉・中葉）と、間脳腹側由来の神経組織である神経性下垂体（後葉）から構成されます。"),
    (21, "頸神経叢", "select_correct", False,
     "横隔膜を運動支配する横隔神経（C3〜C5）は「頸神経叢」から起始します。"),
    (22, "ブローカの中枢", "select_correct", False,
     "運動性言語中枢である「ブローカ中枢（Broca野）」は大脳皮質前頭葉（下前頭回後部）に存在します。感覚性言語中枢（Wernicke野）は側頭葉にあります。"),
    (23, "脊髄神経の前根", "select_correct", False,
     "ベル・マジャンディーの法則により、脊髄前角細胞から出る「前根」は遠心性の運動神経線維のみを通します。後根は求心性の感覚線維、前枝・後枝は混合神経です。"),
    (24, "大腿二頭筋", "select_correct", False,
     "大腿後面筋（ハムストリングス）である「大腿二頭筋（長頭は脛骨神経、短頭は総腓骨神経）」は坐骨神経に支配されます。大殿筋は下殿神経、中殿筋・大腿筋膜張筋は上殿神経支配です。"),
    (25, "中脳水道", "select_correct", False,
     "間脳にある第三脳室と、延髄・橋・小脳の間にある第四脳室を連絡する細い管は中脳内部を貫く「中脳水道（シルビウス水道）」です。")
]

part2_answers = [
    (26, "小脳", "select_correct", False,
     "大脳および「小脳」は、表面（表層）に神経細胞体が集まる灰白質（皮質）が存在し、深部に神経線維が走行する白質（髄質）が位置する構造をとります。脳幹（中脳・橋・延髄）や脊髄は深部に灰白質があります。"),
    (27, "角膜は強膜の続きである。", "select_correct", False,
     "眼球外層（線維膜）は前方1/6の透明な「角膜」と後方5/6の不透明な「強膜」からなり、角膜は強膜の前方への続きです。瞳孔の大きさを変えるのは虹彩、錐状体（錐体細胞）が多いのは黄斑中心窩です。"),
    (28, "三半規管", "select_correct", False,
     "「三半規管（前半規管・後半規管・外側半規管）」は回転加速度を受容する前庭感覚（平衡覚）器官であり、聴覚には関与しません。蝸牛、耳小骨、鼓膜は聴覚器官です。"),
    (29, "脛骨粗面", "select_correct", False,
     "「脛骨粗面」は膝蓋骨の下方、脛骨前面上部に位置し、膝蓋靭帯が停止する骨隆起として体表から明瞭に触知できます。尺骨粗面、橈骨粗面、臀筋粗面は深部にあり体表から触知できません。"),
    (30, "顔面動脈", "select_correct", False,
     "「顔面動脈」は下顎骨下縁を越えて顔面に出る部位（下顎角の前方、咬筋停止部前縁）で体表から容易に拍動を触知できます。"),
    (31, "グルコース", "select_correct", False,
     "血液のpH（7.35〜7.45）を一定に保つ主要な緩衝系には重炭酸緩衝系（HCO3-/H2CO3）、ヘモグロビン緩衝系、血漿蛋白緩衝系、リン酸緩衝系があります。「グルコース」は緩衝作用を持ちません。"),
    (32, "ヘマトクリット ─── 60％", "select_incorrect", False,
     "正常なヘマトクリット値は成人男性で約40〜50％、成人女性で約35〜45％です。「60％」は多血症（赤血球増加症）や高度脱水を示す異常高値です。"),
    (33, "核鎖線維", "select_correct", False,
     "心臓の特殊心筋による興奮伝導系は洞房結節、房室結節、ヒス束、右脚・左脚、プルキンエ線維からなります。「核鎖線維」は骨格筋の筋紡錘を構成する錘内筋線維です。"),
    (34, "ａ、ｂ", "select_correct", False,
     "ヘモグロビンの酸素親和性が高まり酸素解離曲線が左方移動（結合度増加）する要因は、「温度低下（a）」「2,3-DPG減少（b）」「pH上昇（アルカリ化）」「PCO2低下」「酸素分圧上昇」です。"),
    (35, "動脈血 ─── 70mmHg", "select_incorrect", False,
     "正常な動脈血酸素分圧（PaO2）の基準値は約「95〜100mmHg」です。「70mmHg」は低酸素血症を示す異常値です。静脈血PO2は約40mmHg、肺胞気PO2は約100mmHgです。"),
    (36, "安静時吸息 ─── 内肋間筋", "select_incorrect", False,
     "安静時吸息を行う主吸気筋は「横隔膜」および「外肋間筋」です。内肋間筋は努力呼息時に肋骨を引き下げる呼気筋として働きます。"),
    (37, "神経組織", "select_correct", False,
     "脳や中枢神経組織は血液脳関門（BBB）により遊離脂肪酸をほとんど利用できず、エネルギー基質として「グルコース（ブドウ糖）」および飢餓時のケトン体のみを利用してATPを産生します。"),
    (38, "ガストリン", "select_correct", False,
     "「ガストリン」は胃前庭部G細胞から分泌され胃酸分泌を促進する消化管ホルモンです。アミロプシン（膵アミラーゼ）、サッカラーゼ、トリプシンは消化酵素です。"),
    (39, "オッディ括約筋は空腹時に弛緩している。", "select_incorrect", False,
     "オッディ括約筋（十二指腸乳頭括約筋）は空腹時には収縮・閉鎖して胆汁の腸内流入を防ぎ胆嚢内に貯留させます。食後（コレシストキニン分泌時）に弛緩して胆汁・膵液を排出します。"),
    (40, "月経周期では卵胞期に高くなる。", "select_incorrect", False,
     "女性の基礎体温は、排卵後の黄体期にプロゲステロン（黄体ホルモン）の作用により「高温相」となります。卵胞期（排卵前）は「低温相」を示します。"),
    (41, "橋", "select_correct", False,
     "排尿の協調反射中枢（排尿中枢：Barrington核）は脳幹の「橋」に存在し、仙髄排尿反射を制御して膀胱排尿筋の収縮と尿道括約筋の弛緩を調和させます。"),
    (42, "ａ、ｄ", "select_correct", False,
     "アドレナリンは主にβ1受容体（心拍数増加）およびβ2受容体（血管拡張・末梢抵抗低下）を刺激するのに対し、ノルアドレナリンは強いα1作用により末梢循環抵抗を著しく上昇させ、圧受容器反射を介して「心拍数（a）」を減少させ、「末梢循環抵抗（d）」を上昇させます。"),
    (43, "オキシトシン", "select_correct", False,
     "「オキシトシン」およびバソプレッシン（抗利尿ホルモン：ADH）は視床下部で産生され「下垂体後葉」から分泌される後葉ホルモンです。"),
    (44, "エストロジェン", "select_correct", False,
     "コレステロール骨格（ステロイド環）を持つステロイドホルモンは、卵巣から分泌される「エストロジェン（卵胞ホルモン）」、プロゲステロン、副腎皮質ホルモン、テストステロンです。"),
    (45, "ｂ、ｃ", "select_correct", False,
     "低カルシウム血症では、細胞膜のナトリウム透過性が亢進して「神経の興奮性が上昇（c：テタニー）」し、同時にシナプス伝達や「神経-筋接合部でのアセチルコリン放出が抑制（b）」されます。"),
    (46, "逃避反射消失", "select_correct", False,
     "急性脊髄横断損傷直後に生じる脊髄ショック期には、損傷レベル以下の全ての脊髄反射（腱反射、逃避反射、排尿反射等）および筋緊張が一時的に完全「消失（弛緩性麻痺）」します。"),
    (47, "交感神経節前線維 ─── ノルアドレナリン", "select_incorrect", False,
     "交感神経および副交感神経の「節前線維」終末から分泌される神経伝達物質はいずれも「アセチルコリン（ACh）」です。ノルアドレナリンを分泌するのは交感神経節後線維（汗腺等を除く）です。"),
    (48, "クロナキシーは電流値で表す。", "select_incorrect", False,
     "基電流（レオベース）の2倍の強さの刺激で興奮を引き起こすのに必要な最小刺激時間を「クロナキシー（時値）」といい、「時間（ミリ秒など）」の単位で表されます。"),
    (49, "側頭葉障害 ─── 触覚認知不能", "select_incorrect", False,
     "体性感覚や触覚の認知・統合（立体認知や身体部位認知）を司るのは「頭頂葉（一次感覚野および頭頂連合野）」です。側頭葉は聴覚や言語理解（Wernicke野）、記憶を司ります。"),
    (50, "ＴＣＡサイクル ─── ゴルジ装置", "select_incorrect", False,
     "TCAサイクル（クエン酸回路）が行われる細胞小器官は「ミトコンドリア（マトリックス）」です。ゴルジ装置は蛋白質の修飾・選別・分泌に関与します。")
]

if __name__ == "__main__":
    create_patches_for_part(1, part1_answers)
    create_patches_for_part(2, part2_answers)
