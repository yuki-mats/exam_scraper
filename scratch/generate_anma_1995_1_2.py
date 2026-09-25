import json
import os
from scripts.common.question_identity import review_question_id

def create_patches_for_part(part_num, answers_data):
    source_file = f"output/anma/questions_json/1995/00_source/question_1995_{part_num}.json"
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

    base_dir = "output/anma/questions_json/1995"
    os.makedirs(f"{base_dir}/10_questionType_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/15_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/23_correctChoiceText_fixed", exist_ok=True)
    os.makedirs(f"{base_dir}/21_explanationText_added", exist_ok=True)

    with open(f"{base_dir}/10_questionType_fixed/question_1995_{part_num}_questionType_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_10, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/15_correctChoiceText_fixed/question_1995_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_15, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/23_correctChoiceText_fixed/question_1995_{part_num}_correctChoiceText_fixed.json", "w", encoding="utf-8") as f:
        json.dump(patch_23, f, ensure_ascii=False, indent=2)

    with open(f"{base_dir}/21_explanationText_added/question_1995_{part_num}_explanationText_added.json", "w", encoding="utf-8") as f:
        json.dump(patch_21, f, ensure_ascii=False, indent=2)

    print(f"Generated all 4 layers of patches for question_1995_{part_num}.json successfully!")


part1_answers = [
    (1, "看護婦（士）", "select_correct", False,
     "日本の医療関係職種の中で従事者数が最も多いのは「看護婦（士）」（現・看護師）です。医師や歯科医師、薬剤師、あん摩マッサージ指圧師等と比較して最大の就業者数を占めます。"),
    (2, "百日咳", "select_correct", False,
     "三種混合ワクチン（DPTワクチン）の対象疾患は、ジフテリア（Diphtheria）、百日咳（Pertussis）、破傷風（Tetanus）の3疾患です。結核はBCG、麻疹・風疹はMRワクチン等で予防します。"),
    (3, "インフルエンザ", "select_correct", False,
     "インフルエンザはインフルエンザウイルスによる感染症であり、飛沫感染や接触感染で伝播します。赤痢・コレラ・サルモネラ症・A型肝炎等のような経口感染（消化器系感染症）が主経路ではありません。"),
    (4, "ホルマリン消毒", "select_correct", False,
     "ホルマリン消毒はホルムアルデヒドガス等の化学物質を用いる化学的消毒法です。日光消毒、焼却、乾熱滅菌、高圧蒸気滅菌、煮沸消毒などは物理的因子を用いる理学的消毒法（物理的消毒滅菌法）に分類されます。"),
    (5, "真菌に対する殺菌効果は高い。", "select_incorrect", False,
     "消毒用エタノール（約70〜80％水溶液）は一般細菌や多くのウイルスに対して有効ですが、芽胞には無効であり、真菌や一部の親水性ウイルスに対する効果は限定的・中等度です。「真菌に対する殺菌効果が高い」とする記述は誤りです。"),
    (6, "早期発見", "select_correct", False,
     "予防医学における第二次予防は「早期発見・早期治療」です。健康教育、予防接種、栄養改善等は発症そのものを防ぐ「第一次予防」であり、リハビリテーションや社会復帰支援は「第三次予防」に分類されます。"),
    (7, "日本", "select_correct", False,
     "1990年代当時、日本の成人男性喫煙率は先進国（アメリカ、イギリス、スウェーデン等）と比較して依然として高い水準（約50％以上）を維持していました。"),
    (8, "塩素消毒", "select_correct", False,
     "水道水の安全性を確保するための消毒法として、水道法により遊離残留塩素を保持させる「塩素消毒」が義務付けられています。"),
    (9, "放射線", "select_correct", False,
     "公害対策基本法（現・環境基本法）で規定された「典型7公害」は大気汚染、水質汚濁、土壌汚染、騒音、振動、地盤沈下、悪臭の7つです。放射線障害は原子力基本法等の別体系で管理されており、典型7公害には含まれません。"),
    (10, "環境衛生監視員", "select_correct", False,
     "環境衛生監視員は自治体（保健所等）に所属する公務員（行政機関の職員）であり、企業内の労働衛生管理体制（産業医、衛生管理者、衛生推進者、作業主任者等）の従事者ではありません。"),
    (11, "腰痛症 ─ 振動", "select_incorrect", False,
     "局所振動障害（振動病）はチェーンソー等の使用により手指の白蝋病や末梢神経・血流障害を引き起こします。腰痛症の主な発生要因は重量物運搬や不自然な姿勢、長時間の座位・立位などであり、振動との直接の組合せは不適切です。"),
    (12, "施術の方法", "select_correct", False,
     "あん摩マッサージ指圧師、はり師、きゅう師等に関する法律第7条において、施術所の広告可能事項は業務の種類、施術者の氏名・住所、施術日・時間、予約制の有無、出張施術の有無等に限定されており、「施術の方法」や経歴・効能などは広告が禁止されています。"),
    (13, "老人保健施設", "select_correct", False,
     "老人保健法（現・高齢者医療確保法および介護保険法体制）に規定されていた医療施設・療養施設は「老人保健施設」（現・介護老人保健施設）です。特別養護老人ホームや有料老人ホームは老人福祉法に規定されています。"),
    (14, ["聴力障害者", "素行が著しく不良である者", "伝染性の疾病にかかっている者"], "select_incorrect", False,
     "法改正により欠格事由の見直しが行われ、聴力障害者、素行不良者、伝染性疾患罹患者などは絶対的欠格事由から除外または規定が改変されました。本問では1, 2, 3が欠格事由に該当しないものとして正解となります。"),
    (15, ["児童福祉法", "身体障害者福祉法"], "select_correct", False,
     "身体障害者に対する補装具の交付・給付の根拠法は「身体障害者福祉法」および障害児に対する「児童福祉法」（現・障害者総合支援法へ移行）です。そのため選択肢2および3の双方が正解となります。"),
    (16, "幽門括約筋", "select_correct", False,
     "幽門括約筋は消化管壁の平滑筋（不随意筋）が肥厚して形成された括約筋です。口輪筋（顔面神経支配）、咽頭収縮筋（迷走神経支配）、外肛門括約筋（陰部神経支配）はいずれも横紋筋（随意筋）です。"),
    (17, "腎臓 － 骨盤腔", "select_incorrect", False,
     "腎臓は後腹膜器官であり、腹腔の後壁（第12胸椎〜第3腰椎の高さ）に位置します。骨盤腔内に位置するのは膀胱、子宮、直腸などであり、「腎臓 － 骨盤腔」の組合せは誤りです。"),
    (18, "顎関節", "select_correct", False,
     "関節腔内に関節円板（線維軟骨）を有する代表的な関節は「顎関節」「胸鎖関節」「肩鎖関節」「下橈尺関節」などです。膝関節にあるのは関節半月（半月板）であり、肩関節や股関節には関節唇が存在します。"),
    (19, "側頭骨", "select_correct", False,
     "眼窩は前頭骨、頬骨、上顎骨、蝶形骨、口蓋骨、涙骨、篩骨の7個の骨で構成されます。側頭骨は頭蓋の側頭部および耳の構造を形成し、眼窩の構成には関与しません。"),
    (20, "広背筋", "select_correct", False,
     "広背筋は下位胸椎・腰椎の棘突起、仙骨正中稜、腸骨稜等から起こり、上腕骨の小結節稜に停止する浅背筋です。僧帽筋は鎖骨・肩峰・肩甲棘、肩甲挙筋および菱形筋は肩甲骨の内側縁に停止します。"),
    (21, "内転 － 大胸筋", "select_correct", False,
     "大胸筋の主な作用は肩関節の屈曲・内転・内旋です。肩甲下筋は内旋、上腕三頭筋長頭は伸展・内転、棘下筋は外旋に作用します。"),
    (22, "筋皮神経", "select_correct", False,
     "上腕前区の屈筋群（上腕二頭筋、烏口腕筋、上腕筋）は「筋皮神経」によって支配されます。正中神経は前腕屈筋群、尺骨神経は手内筋等、腋窩神経は三角筋・小円筋を支配します。"),
    (23, "主にカルシウムでつくられている。", "select_correct", False,
     "歯の主成分であるエナメル質・象牙質・セメント質は、リン酸カルシウム（ハイドロキシアパタイト）などのカルシウム塩を中心とする無機質で構成されています。永久歯は32本（乳歯は20本）、下顎歯の痛覚は三叉神経第3枝（下顎神経）が伝えます。"),
    (24, "小腸との間に回盲弁がある。", "select_correct", False,
     "回腸の末端が大腸（盲腸）に開口する部分には内容物の逆流を防ぐ「回盲弁（バウヒン弁）」があります。大腸の長さは約1.5〜1.6メートルであり、虫垂は盲腸に付属し、結腸ヒモは盲腸・上行結腸・横行結腸・下行結腸・S状結腸に存在します。"),
    (25, "鼻腔", "select_correct", False,
     "口腔の天井をなす硬口蓋および軟口蓋は、上方の「鼻腔」と下方の口腔を隔てる隔壁となっています。")
]

part2_answers = [
    (26, "皮質", "select_correct", False,
     "腎小体（マルピーギ小体：糸球体とボウマン嚢）および近位・遠位尿細管は、主に腎臓の「皮質」に分布しています。髄質には主にヘンレ係程（ループ）や集合管が走行します。"),
    (27, "大前庭腺", "select_correct", False,
     "大前庭腺（バルトリン腺）は女性の前庭後外側にある粘液分泌腺で、女性に固有の構造です。男性の尿道球腺（カウパー腺）や前立腺に相当します。"),
    (28, "甲状腺", "select_correct", False,
     "甲状腺はチロキシンやカルシトニンを血中に分泌する「内分泌腺」です。乳腺、汗腺、子宮腺（粘膜腺）は導管を通じて体表面や内腔に分泌物を出す「外分泌腺」です。"),
    (29, "大静脈", "select_correct", False,
     "全身を循環した静脈血は上大静脈および下大静脈を通じて「右心房」へ流入します。肺動脈は右心室から肺へ、肺静脈は左心房へ、大動脈は左心室から全身へ血液を送ります。"),
    (30, "冠状動脈は心臓を栄養する。", "select_correct", False,
     "心臓の筋組織（心筋）自身を栄養・酸素供給する血管は、大動脈基部から分岐する左右の「冠状動脈」です。房室弁は尖弁（僧帽弁・三尖弁）であり、左心室壁は右心室壁の約3倍厚く、心尖部は左前下方へ傾きます。"),
    (31, "顔面神経 － 顔面の皮膚", "select_incorrect", False,
     "顔面の皮膚感覚（知覚）を支配するのは「三叉神経」です。顔面神経は顔面の表情筋の運動支配、舌前2/3の味覚、涙腺・唾液腺の分泌（副交感神経）を担います。"),
    (32, "筋裂孔", "select_correct", False,
     "鼡径靭帯の下側は腸恥筋膜弓によって筋裂孔（外側）と血管裂孔（内側）に分かれます。大腿神経と腸腰筋は「筋裂孔」を通過し、大腿動脈・大腿静脈・大腿輪は血管裂孔を通過します。"),
    (33, "延髄", "select_correct", False,
     "随意運動を伝える皮質脊髄路（錐体路）の線維の約80〜90％は「延髄」の下端腹側で左右反対側へ交叉し、これを「錐体交叉」と呼びます。"),
    (34, "虹彩", "select_correct", False,
     "虹彩は瞳孔括約筋（副交感神経支配）と瞳孔散大筋（交感神経支配）により瞳孔径を変化させ、眼球内に入る光量を調節する絞りの役割を果たします。"),
    (35, "蝸牛管", "select_correct", False,
     "内耳のうち聴覚受容器であるラセン器（コルチ器）が存在するのは「蝸牛管」です。卵形嚢・球形嚢は直線加速度（平衡覚）、半規管は回転加速度（平衡覚）を受容します。"),
    (36, "鼡径靱帯", "select_correct", False,
     "大腿三角（スカルパ三角）は、上辺を「鼡径靱帯」、外側縁を「縫工筋」、内側縁を「長内転筋」が構成します。"),
    (37, "上腕動脈", "select_correct", False,
     "上腕内側にある内側上腕二頭筋溝の深部には「上腕動脈」「上腕静脈」「正中神経」が走行しており、脈拍の触知部位としても重要です。"),
    (38, "長腓骨筋腱", "select_correct", False,
     "外果の後方を通過するのは「長腓骨筋腱」および「短腓骨筋腱」です。内果の後方（屈筋支帯下）を通過するのは後脛骨筋腱、長趾屈筋腱、後脛骨動静脈、脛骨神経、長母趾屈筋腱です。"),
    (39, "カリウムイオン", "select_correct", False,
     "生体の電解質分布において、細胞内液で最も濃度が高い陽イオンは「カリウムイオン（K+）」です。細胞外液（血漿・間質液）で最も高い陽イオンは「ナトリウムイオン（Na+）」です。"),
    (40, "アルブミン", "select_correct", False,
     "血漿蛋白質の中で最も含有量が多く（全体の約60％）、血漿膠質浸透圧の維持や物質輸送に主要な役割を果たすのは「アルブミン」です。"),
    (41, "細動脈", "select_correct", False,
     "全身の末梢血管抵抗を調節する主要な抵抗血管であり、血管平滑筋に対する血管運動神経（交感神経血管収縮線維）の分布が最も密なのは「細動脈」です。"),
    (42, "平滑筋である。", "select_incorrect", False,
     "心筋は横紋構造を持つ「横紋筋」でありながら、意識的に動かせない「不随意筋」です。自律神経支配を受け、特殊心筋による自動能（歩調取り機能）を有します。"),
    (43, "駆出期", "select_correct", False,
     "心室の内圧が動脈圧を超えて大動脈弁・肺動脈弁が開き、血液が動脈へ勢いよく押し出される「駆出期」において血圧は最高値（収縮期血圧）に達します。"),
    (44, "胸郭が広がる。", "select_correct", False,
     "外肋間筋が収縮すると肋骨が引き上げられ、胸郭が前後に広がって胸腔内圧が陰圧化し、吸息が起こります。"),
    (45, "でんぷん", "select_correct", False,
     "唾液に含まれる消化酵素「プチアリン（唾液アミラーゼ）」は、多糖類である「でんぷん」を加水分解してマルトース（麦芽糖）やデキストリンに分解します。"),
    (46, "膵液", "select_correct", False,
     "セクレチンは十二指腸粘膜のS細胞から分泌される消化管ホルモンで、膵臓に作用して重炭酸塩（HCO3-）に富むアルカリ性の「膵液」の分泌を促進します。"),
    (47, "糸球体", "select_correct", False,
     "腎動脈から流入した血液は、毛細血管網である「糸球体」において高い有効ろ過圧によりボウマン嚢腔へとろ過され、原尿が生成されます。"),
    (48, "ブドウ糖", "select_correct", False,
     "健康成人において、原尿中にろ過された「ブドウ糖（グルコース）」やアミノ酸は、近位尿細管の能動輸送担体によりほぼ100％再吸収されて尿中には排出されません。"),
    (49, "グルカゴン", "select_correct", False,
     "膵臓ランゲルハンス島A細胞（α細胞）から分泌される「グルカゴン」は、肝臓でのグリコーゲン分解および糖新生を促進して血糖値を上昇させます。インスリンは血糖降下ホルモンです。"),
    (50, "黄体ホルモン", "select_correct", False,
     "卵巣の黄体や胎盤から分泌される「黄体ホルモン（プロゲステロン）」は、子宮内膜を分泌期に維持し、子宮筋の収縮を抑制して妊娠の成立・維持に不可欠な役割を果たします。")
]

if __name__ == "__main__":
    create_patches_for_part(1, part1_answers)
    create_patches_for_part(2, part2_answers)
