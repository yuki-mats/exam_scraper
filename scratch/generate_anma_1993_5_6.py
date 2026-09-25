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


part5_answers = [
    (101, "心", "select_correct", False,
     "五臓のうち血脈を主り、精神・意識・思惟活動（神明）をつかさどるのは「心」です。"),
    (102, "脾", "select_correct", False,
     "水穀の運化をつかさどり、後天の本として気血を生生成・統血する五臓は「脾」です。"),
    (103, "胆", "select_correct", False,
     "六腑の中で決断をつかさどり「中正の官」とされるのは「胆」です。"),
    (104, "汗", "select_correct", False,
     "五液の配当において「心は汗」「肺は涕」「肝は涙」「脾は涎」「腎は唾」をつかさどります。"),
    (105, "骨", "select_correct", False,
     "五体の配当において「腎は骨」「肝は筋」「心は脈」「脾は肉」「肺は皮毛」をつかさどります。"),
    (106, "陽虚", "select_correct", False,
     "陽気の不足（陽虚）により温煦作用が低下すると「陽虚生外寒」となり、四肢の冷えや自汗、寒がりを呈します。"),
    (107, "腎", "select_correct", False,
     "五行配当において「水」に属する五臓は「腎」です。"),
    (108, "脾", "select_correct", False,
     "五行配当において「甘」の味（五味）に対応する五臓は「脾」です。"),
    (109, "肝虚", "select_correct", False,
     "相生理論に基づき「虚すればその母を補う」原則に従い、肝（木）の虚証に対しては母である水（腎）の経穴を補います。"),
    (110, "血熱", "select_correct", False,
     "熱邪が血分に侵入し血行を過剰に亢進させて脈外に溢れ出させる病態を「血熱」といい、各種の出血症状を引き起こします。"),
    (111, "寒邪", "select_correct", False,
     "六淫のうち凝滞性と収引性を特徴とし、気血の運行を停滞させて痛みを起こすのは「寒邪」です。"),
    (112, "悲", "select_correct", False,
     "七情のうち、過度の「悲」は気の消沈・消耗をもたらし、「悲しめば則ち気消す」とされます。"),
    (113, "猛暑の中でテニスをした。", "select_correct", False,
     "「暑邪」は夏季の炎熱・猛暑の気によって発症する外邪であり、酷暑の環境下での活動が直接の誘因となります。"),
    (114, "聞診 － 脈状", "select_incorrect", False,
     "脈状を診察するのは患者の脈に触れる「切診」です。聞診は声・呼吸音や体臭を診察する方法であり、「聞診 － 脈状」は誤りです。"),
    (115, "手の陽明大腸経", "select_correct", False,
     "示指橈側端（商陽）に始まり、上肢外側前縁を経て顔面の迎香に終わるのは「手の陽明大腸経」です。"),
    (116, "3 本", "select_correct", False,
     "下腿内側を上行する足の三陰経は、足の太陰脾経、足の厥陰肝経、足の少陰腎経の「3本」です。"),
    (117, "陰谷", "select_correct", False,
     "足の少陰腎経の合水穴は膝窩横紋内端にある「陰谷穴」です。"),
    (118, "神門", "select_correct", False,
     "手関節掌側横紋上、尺側手根屈筋腱の橈側縁にある手の少陰心経の原穴は「神門穴」です。"),
    (119, "中脘", "select_correct", False,
     "前正中線上、臍の上方4寸にある任脈の経穴であり、胃の募穴・八会穴（腑会）は「中脘穴」です。"),
    (120, "手の太陽小腸経", "select_correct", False,
     "小指尺側端（少沢）に起こり、前腕後面の尺側を上行して上腕骨内側上顆と肘頭の間の尺骨神経溝（小海）を通るのは「手の太陽小腸経」です。"),
    (121, "委中", "select_correct", False,
     "膝窩横紋の中央に位置する「委中穴」は下肢（足の太陽膀胱経）にある経穴です。中極・大横・不容は腹部にあります。"),
    (122, "身柱", "select_correct", False,
     "第3胸椎棘突起下縁の陥凹部に位置する督脈の経穴は「身柱穴」です。大椎は第7頸椎棘突起下、肺兪は第3胸椎下外方1寸5分に位置します。"),
    (123, "足の厥陰肝経 ― 太衝", "select_correct", False,
     "足の厥陰肝経の原穴（兪土穴）は「太衝穴」です。肺経の原穴は太淵、大腸経は合谷、胃経は衝陽です。"),
    (124, "曲沢", "select_correct", False,
     "肘窩横紋上、上腕二頭筋腱の尺側縁に位置する手の厥陰心包経の合水穴は「曲沢穴」です。曲池は肘窩外側端、陽池・陽谷は手関節背側にあります。"),
    (125, "体熱の放散の大部分は呼気から行われる。", "select_incorrect", False,
     "体熱放散の大部分（約70〜80％）は皮膚表面からの「放射・伝導・対流および発汗蒸発」によって行われます。呼気からの熱放散は全体の十数％程度にすぎません。")
]

part6_answers = [
    (126, "圧迫法", "select_correct", False,
     "あん摩（按按・按圧）、マッサージ（プレッション）、指圧の三大手技療法すべてにおいて共通して用いられる基本手技は「圧迫法」です。"),
    (127, "圧迫法", "select_correct", False,
     "神経痛や局所筋痙攣などの神経・筋の異常興奮に対して、持続的な「圧迫法（持続圧・重圧法）」を加えることで求心性インパルスを抑制し鎮静効果を得ることができます。"),
    (128, "汎適応症候群の学説（ストレス学説）", "select_correct", False,
     "セリエ（H. Selye）が提唱した「ストレス学説（汎適応症候群）」では、施術刺激が適度なストレスストレッサーとして視床下部-下垂体-副腎皮質系（HPA軸）を刺激し、副腎皮質ホルモン等の分泌を促して抗ストレス・生体防御反応を高めると説明されます。"),
    (129, "興奮作用", "select_correct", False,
     "運動麻痺や知覚鈍麻などの神経・筋機能の低下に対して、速い軽擦法やリズミカルな叩打法・振戦法などの適度な刺激を与えて生理的機能を呼び覚まし亢進させる作用を「興奮作用」と呼びます。"),
    (130, "機械的・化学的・温度的刺激のいずれにも反応する。", "select_correct", False,
     "ポリモーダル受容器（C線維終末等の侵害受容体）は、強い機械的刺激、有害な熱・冷刺激（温度刺激）、および炎症関連物質（ブラジキニン、プロスタグランジン等の化学刺激）のいずれの刺激にも幅広く反応する特徴を持ちます。"),
    (131, "腹膜炎", "select_correct", False,
     "「腹膜炎」は急性腹症であり、手技による刺激は炎症の波及やショックを誘発するため絶対的禁忌症です。"),
    (132, "揉捏法", "select_correct", False,
     "古方あん摩の「解釈（かいしゃく）の術」は、筋肉をつかんで解きほぐす現在の手技における「揉捏法」に相当します。"),
    (133, "皮膚血管の収縮", "select_incorrect", False,
     "按撫法（軽擦法）の皮膚刺激により、軸索反射や局所ヒスタミン様物質の遊離を介して毛細血管が拡張し、皮膚温の上昇と血行促進が起こります。「皮膚血管の収縮」をもたらすわけではありません。"),
    (134, "按捏法（強擦法）", "select_correct", False,
     "関節周囲の靭帯・腱・関節包の癒着や線維性瘢痕を物理的に解離・剥離させるために最も適した手技は「按捏法（強擦法・フリクション）」です。"),
    (135, "急激な著しい体重減少を伴う疲労", "select_correct", False,
     "「急激な著しい体重減少を伴う全身倦怠感・疲労」は、悪性腫瘍、重症糖尿病、甲状腺機能亢進症、結核などの重大な器質的疾患が強く疑われるため、安易な施術を行わず直ちに医療機関への受診を勧める必要があります。"),
    (136, "ウイリアムズ体操が有効である。", "select_incorrect", False,
     "ウィリアムズ体操は「腰痛症」に対する運動療法（腰椎前弯の軽減・腹筋殿筋強化）です。肩こりの運動療法としては頸肩部のストレッチや肩甲骨周囲の体操（コッドマン体操・棒体操等）が用いられます。"),
    (137, "変形性膝関節症", "select_correct", False,
     "「変形性膝関節症」において、膝関節の荷重支持性と安定性を高め疼痛を軽減するために最も有効な運動療法は「大腿四頭筋の強化訓練（パテラセッティング等）」です。"),
    (138, "肩関節周囲炎", "select_correct", False,
     "アイロン体操とも呼ばれる「コッドマン体操（振り子運動）」は、上肢の重みを利用して肩関節包や腱板を免荷しながら可動域を拡大する「肩関節周囲炎」の代表的運動療法です。"),
    (139, "下腿三頭筋の循環を促進する。", "select_incorrect", False,
     "腓骨神経麻痺では下腿前外側筋群（前脛骨筋等）が麻痺して下垂足となり、拮抗筋である下腿三頭筋や後脛骨筋が短縮・拘縮しやすくなります。治療目的は麻痺筋の機能回復と下腿前外側の血流改善であり、「下腿三頭筋の循環促進」を主眼とするわけではありません。"),
    (140, "発作時は頭部を温める。", "select_incorrect", False,
     "片頭痛（発作性の拍動性頭痛）は頭蓋外血管の異常拡張が一因であるため、発作時に頭部を温めると血管がさらに拡張して激痛が増悪します。発作時は「局所の冷却（アイシング）や圧迫、暗所での安静」が適切です。"),
    (141, "睡眠前の叩打、振せんを主としたマッサージ施術", "select_incorrect", False,
     "就寝直前に強い叩打法や振戦法を行うと、交感神経が刺激・興奮して覚醒度が高まり、不眠を悪化させます。就寝前には副交感神経を優位にする静かな軽擦法や愛護的な指圧が適しています。"),
    (142, "疼痛部位に叩打法を行う。", "select_incorrect", False,
     "神経痛の活動性の疼痛部位に対して強い機械的振動を与える「叩打法」を行うと、過敏化した神経線維を直接刺激して疼痛発作を誘発・悪化させるため不適切（禁忌）です。持続的圧迫や愛護的按撫法が用いられます。"),
    (143, "原則として局所の積極的な施術を第一とする。", "select_incorrect", False,
     "高齢者が尻もちをついた後の急性腰痛では「腰椎圧迫骨折」の可能性が極めて高いため、局所への積極的な施術は骨折部を転位させ神経損傷を招く危険があります。安静を保ち速やかに専門医へ対診することが第一です。"),
    (144, "起坐姿勢での背部の按撫", "select_correct", False,
     "気管支喘息発作中の患者は呼吸困難のため起坐呼吸をとっていることが多く、この姿勢を保ったまま背部への愛護的な按撫法（軽擦）を行うことで、呼吸補助筋の緊張緩和と精神的安心感をもたらすことができます。"),
    (145, "軽い全身マッサージ", "select_correct", False,
     "高齢の高血圧患者に対しては、急激な循環動態の変動や血圧上昇を避けるため、強刺激や長時間の施術は避け、「軽い全身マッサージ」によるリラクゼーションと末梢循環改善が最も安全かつ適切です。"),
    (146, "ワレー圧痛点の持続的圧迫法", "select_correct", False,
     "洗顔や接触により下眼瞼から上唇（三叉神経第2枝：上顎神経領域）に生じる発作的激痛は「三叉神経痛」の特徴であり、眼窩下孔などのワレー圧痛点に対する愛護的な「持続的圧迫法」による知覚神経の過剰興奮抑制が有効です。"),
    (147, "境界域高血圧症で運動不足を指摘されている場合", "select_correct", False,
     "心不全疑いの浮腫や脳血管障害疑いの頭痛・めまいを伴う高血圧は医療管理が優先されます。合併症のない「境界域高血圧症で運動不足」のケースは、自律神経調整と循環改善を目的とした手技療法の最も安全で適応性の高い状態です。"),
    (148, "メニエール病と考え頸肩背部のマッサージを行う。", "select_correct", False,
     "発作的な回転性めまい、耳鳴、難聴の3主徴は「メニエール病」を示唆しており、頸肩背部の筋緊張を緩和して頭頸部・内耳への血流改善を図るマッサージ施術が効果的です。"),
    (149, "筋力の強化", "select_correct", False,
     "マッサージの主たる生理的作用は循環改善、筋緊張の緩和、疲労回復、柔軟性の向上などです。受動的な施術であるマッサージ単独によって「筋力を強化する（筋肥大させる）」ことはできません。"),
    (150, "心因性の場合", "select_correct", False,
     "腹部診察において筋性防御や黄疸、発熱・下痢・嘔吐などの急性炎症・器質的病変を伴う腹痛は施術禁忌です。ストレスや自律神経失調に伴う「心因性腹痛（機能性腹痛・過敏性腸症候群等）」は手技療法の好適応となります。")
]

if __name__ == "__main__":
    create_patches_for_part(5, part5_answers)
    create_patches_for_part(6, part6_answers)
