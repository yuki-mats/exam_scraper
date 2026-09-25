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


part5_answers = [
    (101, "心", "select_correct", False,
     "五臓のうち、血脈を統括し精神・意識・思惟活動をつかさどる（神を蔵する）のは「心」です。"),
    (102, "気滞", "select_correct", False,
     "胸脇部の張るような痛み（脹痛）や精神的鬱悶、情動変化によって増減する症状は、気の運行が停滞する「気滞（気鬱）」の病態です。"),
    (103, "胆", "select_correct", False,
     "五臓六腑の中で決断をつかさどり「中正の官」と呼ばれるのは「胆」です。肝は将軍の官（謀慮）、心は君主の官（神明）です。"),
    (104, "汗", "select_correct", False,
     "五液において「心は汗」「肺は涕」「肝は涙」「脾は涎」「腎は唾」をつかさどります。したがって心と関係の深い体液は「汗」です。"),
    (105, "筋", "select_correct", False,
     "五体において「肝は筋」「心は脈」「脾は肉」「肺は皮毛」「腎は骨」をつかさどります。したがって肝が主るのは「筋」です。"),
    (106, "陽虚", "select_correct", False,
     "陽気の不足（陽虚）により温煦作用が低下すると「陽虚生外寒」となり、四肢の冷え、自汗、畏寒（寒がり）などの症状が現れます。"),
    (107, "腎", "select_correct", False,
     "五行配当において「水」に属する五臓は「腎」です（木＝肝、火＝心、土＝脾、金＝肺、水＝腎）。"),
    (108, "脾", "select_correct", False,
     "五行配当において「甘」の味覚（五味）に対応する五臓は「脾」です（酸＝肝、苦＝心、甘＝脾、辛＝肺、鹹＝腎）。"),
    (109, "肝虚", "select_correct", False,
     "相生関係（母子関係）において「虚すればその母を補う」という原則に基づき、肝の虚証（子）に対しては母である「水（腎）」の経穴（曲泉・陰谷等）を補います。本問では肝の虚証（肝虚）の治療方針を示しています。"),
    (110, "血熱", "select_correct", False,
     "熱邪が血分に入り血行が異常に亢進して血管外に溢れ出る病態を「血熱」といい、吐血、鼻出血（衄血）、皮下出血、鮮紅色の不正出血などを引き起こします。"),
    (111, "寒邪", "select_correct", False,
     "六淫のうち、陰邪であり凝滞性（気血の流れを滞らせて痛みを起こす）と収引性（筋・脈をひきつらせ縮こまらせる）を特徴とするのは「寒邪」です。"),
    (112, "悲", "select_correct", False,
     "七情のうち、過度の「悲（または憂）」は気の消沈・消耗をもたらし、「悲しめば則ち気消す」とされます。怒＝気上、喜＝気緩、思＝気結、恐＝気下です。"),
    (113, "猛暑の中でテニスをした。", "select_correct", False,
     "「暑邪」は夏季の炎熱・猛暑の気によって発症する外感病邪です。猛暑の屋外で直射日光を受けながら運動することは暑邪を受ける直接の原因となります。"),
    (114, "聞診 － 圧痛", "select_incorrect", False,
     "切診は患者の体に直接触れて脈状や腹部・経穴の圧痛・硬結を診察する手技です。聞診は声・呼吸音・咳や体臭・排泄物の臭いを聞く・嗅ぐ診察法であり、「聞診 － 圧痛」の組合せは誤りです。"),
    (115, "手の陽明大腸経", "select_correct", False,
     "示指橈側端（商陽）に始まり、合谷・曲池・肩髃・側頸部を経て顔面の迎香（鼻翼外方）に終わるのは「手の陽明大腸経」です。"),
    (116, "3 本", "select_correct", False,
     "下腿内側を上行する陰経脈は、足の太陰脾経、足の厥陰肝経、足の少陰腎経の「3本」です。"),
    (117, "原穴", "select_correct", False,
     "合谷穴（手の陽明大腸経）は原気（元気）が注ぐ「原穴」です。大腸経の募穴は天枢、井穴は商陽、郄穴は温溜です。"),
    (118, "命門", "select_correct", False,
     "腎兪穴は第2腰椎棘突起下縁の外方1寸5分に位置し、督脈の「命門穴」（第2腰椎棘突起下）と同じ高さ（第2腰椎レベル）にあります。"),
    (119, "膏肓は第 4 胸椎棘突起の下の外方 3 寸に取る。", "select_correct", False,
     "膏肓穴は「第4胸椎棘突起下縁の外方3寸」に取穴します。肩井は第7頸椎と肩峰を結ぶ線の中点、風門は第2胸椎棘突起下の外方1寸5分、身柱は第3胸椎棘突起下に取ります。"),
    (120, "肩髃", "select_correct", False,
     "「肩髃穴」は肩関節前上方（肩峰外縁前端と大結節の間）にあり、手の陽明大腸経に属します。肩髎は三焦経、肩貞は小腸経、肩中兪は小腸経です。"),
    (121, "三陰交", "select_correct", False,
     "内果尖の上方3寸、脛骨内側面後縁にある経穴は足の太陰脾経の「三陰交穴」です。"),
    (122, "肝兪", "select_correct", False,
     "第9胸椎棘突起下縁の外方1寸5分にある膀胱経の背部兪穴は「肝兪穴」です（膈兪は第7胸椎、脾兪は第11胸椎、胃兪は第12胸椎下外方1.5寸）。"),
    (123, "公孫 ─ 背部", "select_incorrect", False,
     "公孫穴は足の第1中足骨底の前下方、赤白肉際に位置する足の太陰脾経の絡穴（八脈交会穴）であり、足部にあります。背部ではありません。"),
    (124, "委中", "select_correct", False,
     "膝窩横紋の中央に位置する足の太陽膀胱経の合土穴・四総穴は「委中穴」です。"),
    (125, "利関の術 － 運動法", "select_correct", False,
     "古方あん摩の手技である「利関（りかん）の術」は、四肢の関節を動かして気血をめぐらせる「関節他動運動法」に相当します。調摩は軽擦法、解釈は揉捏法、按按は圧迫法に相当します。")
]

part6_answers = [
    (126, "筋線維の増加", "select_incorrect", False,
     "揉捏法は筋組織の循環改善、代謝産物の除去、筋緊張の緩和・疲労回復を促進しますが、施術自体によって「筋線維の数が増加する」ことはありません（筋肥大や筋線維の増生は適切なレジスタンストレーニング等によって生じます）。"),
    (127, "マッサージ － 強擦法", "select_correct", False,
     "強擦法（フリクション）はマッサージの基本手技（軽擦・強擦・揉捏・叩打・振戦・圧迫）の一つです。指髁軽擦法はあん摩、結合織マッサージは特殊手技です。"),
    (128, "漸増漸減圧を加える。", "select_incorrect", False,
     "浪越徳治郎により提唱された指圧の操作三原則は「垂直の原則（垂直圧）」「持続の原則（持続圧）」「集中の原則（集中圧）」の3つです。「漸増漸減」は押圧の力加減・手技上の要素ではありますが、基本三原則の名称規定には含まれません。"),
    (129, "ゲートコントロール説", "select_correct", False,
     "メルザックとウォールが提唱した「ゲートコントロール説」では、太い有髄線維（Aβ線維）の触圧覚刺激が脊髄後角膠様質（SG細胞）を活性化し、侵害受容線維（Aδ・C線維）からの痛覚伝達を抑制（ゲートを閉鎖）して鎮痛をもたらすと説明されます。"),
    (130, "ウィリアムズ体操", "select_correct", False,
     "「ウィリアムズ（Williams）体操」は、腰椎前弯を軽減させ、腹筋・殿筋を強化しハムストリングス・腸腰筋をストレッチする腰痛症の代表的な運動療法です。コッドマン体操は肩関節周囲炎、ボバース法は中枢麻痺に用いられます。"),
    (131, "矯正作用", "select_correct", False,
     "関節拘縮や変形、筋の短縮に対して手技や他動運動を加え、関節可動域の改善や解剖学的アライメントの正常化を図る手技療法の作用は「矯正作用」と呼ばれます。"),
    (132, "胃アトニー", "select_correct", False,
     "胃アトニー（胃無力症・胃下垂）は胃壁平滑筋の緊張低下と蠕動運動減弱による機能性障害であり、腹部や背部への手技療法（軽擦・揉捏・指圧等）により副交感神経機能を高め筋緊張を改善する好適応症です。腹膜炎や急性期狭心症は禁忌です。"),
    (133, "挫手くじき", "select_correct", False,
     "古方あん摩の曲手（きょくて）のうち、「挫手（くじきて）」は母指を用いて筋や腱を弾くように圧迫・刺激する手技です。"),
    (134, "温罨法", "select_correct", False,
     "乳房マッサージ（桶谷式等）の前後には、局所の血流促進と乳管の開通・緊張緩和を図る目的で温タオル等を用いた「温罨法（温熱療法）」が一般的に併用されます。"),
    (135, "腱板断裂", "select_correct", False,
     "急性期の「腱板断裂」は腱組織の構造的断裂損傷であり、局所へのマッサージは損傷を悪化させるため不適当（禁忌）です。整形外科での固定や手術的治療の適応となります。"),
    (136, "肩甲間部", "select_correct", False,
     "大菱形筋・小菱形筋は脊柱（頸・胸椎棘突起）と肩甲骨内側縁の間に張る筋であるため、そのコリに対する施術部位は「肩甲間部」です。"),
    (137, "斜角筋", "select_correct", False,
     "アレンテスト陽性、鎖骨上窩部の圧迫（モーリーテスト様所見）で上肢に放散痛・しびれが生じる病態は「斜角筋症候群（胸郭出口症候群）」を示しており、施術対象筋は「斜角筋（前・中斜角筋）」です。"),
    (138, "コッドマン体操 － 筋力の強化", "select_incorrect", False,
     "コッドマン体操（アイロン体操・振り子運動）は、体幹を前屈させて上肢を脱力垂下させ、振り子のように揺らすことで肩関節の「関節拘縮の予防・可動域の拡大」を図る他動的・免荷的運動療法であり、筋力強化を主目的とするものではありません。"),
    (139, "柔らかい寝具の使用", "select_incorrect", False,
     "慢性腰痛の患者に柔らかすぎる寝具を使用させると、腰部が沈み込んで腰椎の前弯や屈曲が過度になり力学的負担が増大して症状が悪化します。硬めで適度な弾力性のある寝具が推奨されます。"),
    (140, "顔面筋への叩打法", "select_incorrect", False,
     "緊張型頭痛（筋収縮性頭痛）は後頭部から頸肩部の持続的筋緊張が原因であるため、後頭筋・僧帽筋・頸部筋への揉捏・温熱療法・牽引等が有効です。顔面筋への叩打法は直接の治療として不適切です。"),
    (141, "母指持続圧迫法", "select_incorrect", False,
     "末梢性顔面神経麻痺（ベル麻痺等）の顔面筋マッサージでは、筋線維を傷めないよう愛護的な軽擦法や円運動による揉捏法、軽い叩打法が用いられます。強刺激となる強い「持続圧迫法」は筋損傷や病的共同運動誘発のリスクがあるため避けるべきです。"),
    (142, "橈骨神経", "select_correct", False,
     "手関節の背屈（伸展）およびMP関節の伸展運動を担う伸筋群は「橈骨神経」支配です。これが麻痺すると下垂手を呈します。"),
    (143, "排便により軽快する左下腹部の痛み。", "select_correct", False,
     "過敏性腸症候群や痙攣性便秘などによる排便で軽快する左下腹部痛は機能性腸障害であり、手技療法の適応となります。筋性防御、急激な体重減少（器質的疾患・悪性腫瘍疑い）、ブルンベルグ徴候（腹膜炎）は急性腹症・外科的疾患であり施術禁忌です。"),
    (144, "腹筋運動をさせる。", "select_correct", False,
     "弛緩性便秘は腹壁筋力低下や結腸の蠕動不全が主因であるため、腹圧を高め腸運動を促す「腹筋運動の指導」や腹部マッサージが極めて有効です。"),
    (145, "腹部の冷罨法", "select_incorrect", False,
     "慢性胃炎に対しては局所の循環改善と胃機能調整のため温熱療法（温罨法）や愛護的な軽擦・揉捏法が適応となります。血管収縮と消化管機能低下を招く「冷罨法」は不適切です。"),
    (146, "腎経", "select_correct", False,
     "腰部の重だるさ、易疲労感、下腿の冷え、頻尿（小便近し）、目の下のくまなどは、腎精不足・腎陽虚の典型的症候であり、「腎経（足の少陰腎経）」が最も適切な施術対象経絡です。"),
    (147, "股関節の強擦法", "select_incorrect", False,
     "月経困難症に対しては、腹部・腰仙部・大腿内側部（足の三陰経走行部）への愛護的な軽擦・圧迫・揉捏法により骨盤腔内の循環改善と自律神経調整を図ります。股関節部への激しい強擦法は不適切です。"),
    (148, "夜尿症", "select_correct", False,
     "腎盂腎炎や急性尿道炎は活動性尿路感染症、前立腺癌は悪性腫瘍であり手技療法の適応外です。機能的排尿障害である「夜尿症（小児の夜尿）」は仙骨部や下腹部への施術が適応となります。"),
    (149, "アイシング", "select_incorrect", False,
     "競技前（ウォーミングアップ時）には筋温・腱の柔軟性を高めて血流を促進するため、ストレッチング、マッサージ、テーピング等が有効です。「アイシング（冷却）」は筋腱の伸展性を低下させ障害リスクを高めるため、急性炎症や競技後のクールダウン時に行うべきです。"),
    (150, "施術は軽微な力で行う。", "select_correct", False,
     "骨粗鬆症の患者は骨密度が低下し骨脆弱性が高いため、強圧や急激な脊椎矯正法は圧迫骨折を誘発する危険があります。施術は愛護的かつ「軽微な力」で行う必要があります。")
]

if __name__ == "__main__":
    create_patches_for_part(5, part5_answers)
    create_patches_for_part(6, part6_answers)
