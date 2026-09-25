import json
import os
from scripts.common.question_identity import review_question_id

def create_patches_for_part(part_num, answers_data):
    source_file = f"output/anma/questions_json/1993/00_source/question_1993_{part_num}.json"
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

    base_dir = "output/anma/questions_json/1993"
    os.makedirs(f"{base_dir}/10_questionType_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/15_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/23_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/21_explanationText_added", exist_ok=True)

    with open(f"{base_dir}/10_questionType_fixed/question_1993_{part_num}_questionType_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_10, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/15_correctChoiceText_fixed/question_1993_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_15, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/23_correctChoiceText_fixed/question_1993_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_23, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/21_explanationText_added/question_1993_{part_num}_explanationText_added.json", "w", encoding="utf-8") as f:
        json.dump(patch_21, f, ensure_ascii=False, indent=2)

    print(f"Generated all 4 layers of patches for question_1993_{part_num}.json successfully!")


part1_answers = [
    (1, "悪性新生物", "select_correct", False,
     "日本の死因順位において、1981年（昭和56年）以降、第1位を占め続けているのは「悪性新生物（がん）」です。それ以前は結核や脳血管疾患が第1位でした。"),
    (2, "国勢調査", "select_correct", False,
     "ある一時点における人口の規模や構成（年齢、性別、就業状態等）を把握する人口静態統計の代表例は「国勢調査」です。出生、死亡、婚姻、離婚などを記録する統計は人口動態統計です。"),
    (3, "高圧蒸気滅菌", "select_correct", False,
     "高圧蒸気滅菌（オートクレーブ）は熱と圧力という物理的因子を用いる「物理的（理学的）滅菌法」です。逆性石けん、塩素系消毒剤、エタノールは化学物質を用いる化学的消毒法です。"),
    (4, "緑膿菌", "select_correct", False,
     "「緑膿菌（Pseudomonas aeruginosa）」やMRSA（メチシリン耐性黄色ブドウ球菌）は、易感染性患者や入院患者に感染を引き起こす代表的な院内感染（日和見感染）の起因菌です。"),
    (5, "急性灰白髄炎", "select_correct", False,
     "ポリオ（急性灰白髄炎）はポリオウイルスの「経口感染（糞口感染）」によって消化管から侵入し、脊髄前角細胞を侵して弛緩性麻痺を引き起こす感染症です。"),
    (6, "4000Hz 付近", "select_correct", False,
     "騒音性難聴（職業性難聴）の初期において、聴力低下が特徴的に認められる周波数帯は「4000Hz付近（C5 dip）」です。"),
    (7, "心疾患", "select_correct", False,
     "日本の主要三大死因は「悪性新生物」「心疾患」「脳血管疾患」です。"),
    (8, "地盤沈下", "select_correct", False,
     "公害対策基本法（現・環境基本法）で規定される典型7公害は、大気汚染、水質汚濁、土壌汚染、騒音、振動、地盤沈下、悪臭の7つです。"),
    (9, "学校薬剤師", "select_correct", False,
     "学校保健安全法（旧・学校保健法）に基づき、学校環境衛生（照度、換気、水質、給食衛生等）の維持管理や検査に従事するのは「学校薬剤師」です。"),
    (10, "精神分裂病", "select_correct", False,
     "幻覚（幻聴）や妄想、自我障害、思考障害、感情鈍麻などを主症状とする内因性精神病は「精神分裂病（現・統合失調症）」です。"),
    (11, "施術録の記載", "select_correct", False,
     "医師法における診療録（カルテ）の記載義務と同様、施術に関する義務規定として柔道整復師法等には施術録の記載規定がありますが、あん摩マッサージ指圧師、はり師、きゅう師等に関する法律には「施術録の記載義務」は直接規定されていません（医師の同意書受領、守秘義務、衛生管理義務等が規定されています）。"),
    (12, "開設届の義務", "select_correct", False,
     "あん摩マッサージ指圧師等の施術所を開設したときは、開設後10日以内にその旨を都道府県知事（保健所設置市長・特別区長）に「届出」なければなりません。"),
    (13, "エイズ ― 伝染病予防法", "select_incorrect", False,
     "エイズ（後天性免疫不全症候群）は当時「エイズ予防法（後天性免疫不全症候群の予防に関する法律）」によって管理されていました（現・感染症法）。旧伝染病予防法には規定されていませんでした。"),
    (14, "免許の取消し処分を受けたときは 7 日以内", "select_incorrect", False,
     "あん摩マッサージ指圧師が免許の取消処分を受けたときは、「5日以内」に免許証を厚生労働大臣（指定登録機関）に返納しなければなりません。「7日以内」とする記述は誤りです。"),
    (15, "廃棄物の収集と処分に関する事項", "select_incorrect", False,
     "一般廃棄物の収集・運搬・処分は市町村の清掃部局（環境衛生部局）の責務であり、「保健所」の直接の所管業務ではありません。保健所は広域的・専門的な公衆衛生活動を行います。"),
    (16, "横隔膜", "select_correct", False,
     "「横隔膜」は腱中心とそれを取り囲む骨格筋（横紋筋）から構成される呼吸筋です。脈絡膜、白膜、腹膜は結合組織や上皮組織からなる膜構造です。"),
    (17, "鎖骨 ― 肩峰", "select_incorrect", False,
     "肩峰（Acromion）は「肩甲骨」の肩甲棘外側端にある骨突起です。鎖骨の外側端（肩峰端）と肩鎖関節を形成します。"),
    (18, "大殿筋 ― 大転子", "select_incorrect", False,
     "大殿筋は腸骨翼外面・仙骨後面から起こり、主に大腿骨の「臀筋粗面」および「腸脛靭帯」に停止します。大転子に停止するのは中殿筋、小殿筋、梨状筋などです。"),
    (19, "上腕筋", "select_correct", False,
     "上腕筋は上腕骨前面から尺骨粗面に停止する「肘関節の屈筋」であり、肩関節をまたがないため肩関節の運動（内転等）には関与しません。大胸筋、広背筋、大円筋は肩関節の内転に働きます。"),
    (20, "前脛骨筋", "select_correct", False,
     "下腿前区の伸筋群（前脛骨筋、長趾伸筋、長母趾伸筋）は「深腓骨神経」によって支配されます。腓腹筋・ひらめ筋は脛骨神経、長腓骨筋は浅腓骨神経支配です。"),
    (21, "大腿神経 ― 大腿四頭筋", "select_correct", False,
     "大腿前面の大腿四頭筋・縫工筋・恥骨筋は「大腿神経」によって支配されます。大腿二頭筋は坐骨神経、下腿三頭筋は脛骨神経、大殿筋は下殿神経支配です。"),
    (22, "空腸", "select_correct", False,
     "「空腸」および回腸は長い腸間膜を有し、腹膜腔内で高い可動性を持つ有腸間膜器官（腹膜内器官）です。上行結腸、十二指腸（球部除く）、直腸は後腹膜固定器官です。"),
    (23, "空腸 → 回腸 → 十二指腸", "select_incorrect", False,
     "小腸の解剖学的走行順序は、胃の幽門に続いて「十二指腸 → 空腸 → 回腸」の順です。"),
    (24, "気管 → 声門 → 葉気管支", "select_incorrect", False,
     "声門は「喉頭」内に存在する構造です。気道の順序は「鼻腔 → 咽頭 → 喉頭（声門） → 気管 → 主気管支 → 葉気管支 → 細気管支 → 肺胞」であり、気管の後に声門が位置することはありません。"),
    (25, "十二指腸", "select_correct", False,
     "右腎の前面には肝臓（右葉下面）、結腸右曲部（上行結腸移行部）、および「十二指腸（下行部）」が接しています。胃、脾臓、膵臓は左腎の前面に接します。")
]

part2_answers = [
    (26, "尿管は腎盤（腎盂）と膀胱とを連絡している。", "select_correct", False,
     "尿管は腎盂（腎盤）に始まり、後腹膜を下降して膀胱底の後外側に開口する細長い筋性管です。膀胱三角の上外側角にあるのは左右の尿管口、下角が内尿道口です。"),
    (27, "精子は精巣中で完成される。", "select_correct", False,
     "精子は精巣の精細管（曲精細管）において精祖細胞から減数分裂を経て形成・完成されます。卵子の染色体数は半減して23本（受精卵で46本）、原始卵胞は皮質に存在し、受精は卵管膨大部で行われます。"),
    (28, "膵臓", "select_correct", False,
     "「膵臓」は外分泌腺（膵液を十二指腸に分泌する腺房組織）と内分泌腺（インスリン・グルカゴン等を血中に分泌するランゲルハンス島）の両方を兼ね備えた混合腺です。"),
    (29, "洞房結節 ― 右心房", "select_correct", False,
     "心臓の歩調取り（ペースメーカー）である洞房結節（キース・フラック結節）は、「右心房」の上大静脈開口部付近の心外膜下に位置します。僧帽弁は二尖弁、房室結節は右心房後下部にあります。"),
    (30, "肋間動脈", "select_correct", False,
     "胸大動脈の後壁から直接分岐するのは第3〜第11「後肋間動脈」および肋下動脈です。内胸動脈および椎骨動脈は鎖骨下動脈の枝、腋窩動脈は鎖骨下動脈の続きです。"),
    (31, "総頸動脈の拍動は頸動脈三角の部位で触れる。", "select_correct", False,
     "総頸動脈は胸鎖乳突筋、肩甲舌骨筋上腹、顎二腹筋後腹で囲まれる「頸動脈三角」において体表近くを走行し、拍動を容易に触知できます。眼動脈は内頸動脈の枝、浅側頭動脈は外頸動脈の枝です。"),
    (32, "迷走神経", "select_correct", False,
     "「迷走神経（第Ⅹ脳神経）」は頸静脈孔を出て頸部・胸部・腹部にまで広範囲に下行し、心臓、気管、食道、胃、小腸、結腸右半部に副交感神経線維および感覚線維を分布させます。"),
    (33, "視床は間脳の一部である。", "select_correct", False,
     "間脳は「視床」および「視床下部」から構成されます。視覚野は後頭葉にあり、中心溝は前頭葉と頭頂葉を区切り、第4脳室は延髄・橋と小脳の間にあります。"),
    (34, "橈骨神経", "select_correct", False,
     "「橈骨神経」は腕神経叢後索から起こり、上腕骨背面の「橈骨神経溝」を上腕深動脈とともに回るように斜めに走行して前外側へと向かいます。"),
    (35, "表皮", "select_correct", False,
     "爪（爪板）は「表皮」の角質層が特殊に変形・硬化して形成された皮膚付属器です。"),
    (36, "蝸牛", "select_correct", False,
     "内耳は骨迷路および膜迷路からなり、「蝸牛」「前庭」「半規管」で構成されます。鼓膜・耳小骨は中耳、耳管は中耳と咽頭を連絡する管です。"),
    (37, "肋間神経", "select_correct", False,
     "「肋間神経」は胸神経前枝であり、肋間隙（胸壁）を走行します。縦隔（左右の胸膜腔に挟まれた中央部）を通過するのは心臓、大血管、食道、気管、胸管、迷走神経、横隔神経などです。"),
    (38, "臍静脈は成人では肝円索となっている。", "select_correct", False,
     "胎児循環において酸素に富む血液を胎盤から胎児へ運ぶ臍静脈（1本）は、生後に閉塞・線維化して「肝円索」となります。臍帯には1本の臍静脈と2本の臍動脈が走行します。"),
    (39, "ミトコンドリア", "select_correct", False,
     "細胞小器官のうち、クエン酸回路（TCA回路）および電子伝達系による好気的呼吸を行って大量のATPを産生するエネルギー工場は「ミトコンドリア」です。"),
    (40, "寿命は約 7 日である。", "select_incorrect", False,
     "正常な赤血球の寿命は「約120日」です。脾臓や肝臓のマクロファージによって貪食・破壊されます。寿命約7〜10日なのは血小板です。"),
    (41, "フィブリノーゲン", "select_correct", False,
     "血液凝固の最終段階において、トロンビンの作用により不溶性のフィブリン（線維素）へと変化して血栓を形成する血漿蛋白質は「フィブリノーゲン（第Ⅰ因子）」です。"),
    (42, "洞房結節", "select_correct", False,
     "心臓の刺激伝導系において、最も高い頻度で自動的に興奮リズムを発生させ、心拍の歩調取り（ペースメーカー）として機能するのは「洞房結節（SAノード）」です。"),
    (43, "毛細血管", "select_correct", False,
     "血管壁が単層扁平上皮（内皮）のみで構成され、組織間質液との間で酸素・二酸化炭素のガス交換や栄養素・老廃物の物質交換が行われるのは「毛細血管」です。"),
    (44, "外肋間筋の収縮", "select_correct", False,
     "安静吸息時には、主吸気筋である「横隔膜の収縮（下降）」および「外肋間筋の収縮（肋骨引き上げ・胸郭拡大）」により胸腔内圧が陰圧となって空気が吸入されます。"),
    (45, "リパーゼ ― 脂肪", "select_correct", False,
     "膵液に含まれる消化酵素のうち、「膵リパーゼ」は中性脂肪（トリグリセリド）を加水分解して脂肪酸とモノグリセリドに分解します。アミラーゼはデンプン、トリプシンは蛋白質、ヌクレアーゼは核酸を分解します。"),
    (46, "尿酸が含まれる。", "select_correct", False,
     "健康成人の尿には尿素、尿酸、クレアチニン、電解質などの終末代謝産物が含まれます。正常尿中にはブドウ糖や蛋白質はほとんど排泄されず、pHは食事や代謝により変動します。"),
    (47, "体温調節中枢は視床下部にある。", "select_correct", False,
     "生体の体温調節中枢（セットポイントの設定、熱産生・熱放散の指令）は間脳の「視床下部（視索前野・前視床下部）」に存在します。体温は日内変動（早朝低く夕方高い）があり、直腸温は腋窩温より高くなります。"),
    (48, "インスリン", "select_correct", False,
     "膵臓ランゲルハンス島B細胞（β細胞）から血中に分泌され、血糖値を低下させる唯一のホルモンは「インスリン」です。"),
    (49, "筋の両端を固定した状態で生じる収縮を等張性収縮という。", "select_incorrect", False,
     "筋の両端を固定して筋の長さが変わらないまま張力のみが発生する収縮は「等尺性収縮（アイソメトリック）」です。張力が一定で筋の長さが変化する収縮を「等張性収縮（アイソトニック）」と呼びます。"),
    (50, "アセチルコリン", "select_correct", False,
     "神経筋接合部や副交感神経節後線維終末、自律神経節シナプスなどにおいて放出される代表的な神経伝達物質は「アセチルコリン」です。")
]

if __name__ == "__main__":
    create_patches_for_part(1, part1_answers)
    create_patches_for_part(2, part2_answers)
