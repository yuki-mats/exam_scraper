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


part3_answers = [
    (51, "肝臓", "select_correct", False,
     "体温調節において、安静時に最も多くの代謝熱を産生している臓器は「肝臓」（および骨格筋）です。激しい運動時には骨格筋が最大の熱産生源となります。"),
    (52, "交感神経の活動亢進 － 瞳孔の縮小", "select_incorrect", False,
     "交感神経の活動が亢進すると瞳孔散大筋が収縮し「瞳孔が散大（拡大）」します。瞳孔の縮小（縮瞳）は副交感神経（動眼神経）の活動亢進による作用です。"),
    (53, "網膜", "select_correct", False,
     "眼球壁の最内層である「網膜」には視細胞（錐体細胞・桿体細胞）が存在し、受容した光刺激を活動電位に変換して視神経へと伝達します。"),
    (54, "屈曲反射", "select_correct", False,
     "四肢の皮膚に痛覚刺激（侵害刺激）が加わった際に、四肢を素早く引っ込める「屈曲反射（逃避反射・侵害反射）」は代表的な多シナプス反射（皮膚反射）です。腱反射（膝蓋腱反射・アキレス腱反射）は単シナプス反射です。"),
    (55, "肝臓", "select_correct", False,
     "血液凝固因子の多く（フィブリノーゲン、プロトロンビン、第Ⅶ・Ⅸ・Ⅹ因子等）やアルブミンは「肝臓」で合成・産生されます。"),
    (56, "出血性素因", "select_correct", False,
     "血小板減少症や血液凝固異常症など、出血しやすく止血しにくい全身的な病的状態を「出血性素因」と呼びます。"),
    (57, "浮腫", "select_correct", False,
     "組織間隙（間質組織）や漿膜腔内に異常に多量の細胞外液（水分）が貯留した状態を「浮腫（水腫）」と呼びます。"),
    (58, "壊死", "select_correct", False,
     "生体内の局所組織や細胞が不可逆的な障害を受けて死滅することを「壊死（ネクローシス）」と呼びます。"),
    (59, "脳軟化症は脳梗塞の結果である。", "select_correct", False,
     "脳梗塞（脳血栓症・脳塞栓症）によって脳組織への血流が途絶すると、脳組織は融解壊死を起こして軟化し、これを「脳軟化症」と呼びます。"),
    (60, "心筋梗塞 － 壊死", "select_correct", False,
     "冠状動脈の閉塞により心筋への血液供給が途絶し、心筋組織が不可逆的な虚血性壊死に陥る病態が「心筋梗塞」です。"),
    (61, "ひょう疽", "select_correct", False,
     "「ひょう疽（瘭疽）」は手指や足趾の爪周囲・皮下組織に黄色ブドウ球菌などが感染して生じる急性の化膿性炎症です。結核は肉芽腫性炎、ジフテリアは偽膜性炎です。"),
    (62, "再生上皮", "select_correct", False,
     "創傷治癒の過程で形成される「肉芽組織」の3大構成要素は「毛細血管（新生血管）」「線維芽細胞」「貪食細胞（マクロファージ等の炎症細胞）」です。再生上皮は肉芽組織の表面を覆う上皮化の段階であり、肉芽組織自体の構成要素ではありません。"),
    (63, "悪性上皮性腫瘍を癌腫と呼ぶ。", "select_correct", False,
     "腫瘍の分類において、上皮性組織から発生する悪性腫瘍を「癌腫（がん）」、非上皮性結合組織から発生する悪性腫瘍を「肉腫」と呼びます。良性腫瘍は境界明瞭で膨張性に増殖し転移しません。"),
    (64, "ビタミンＣ", "select_correct", False,
     "先天奇形の発生要因（催奇形因子）には染色体異常・遺伝子異常、母体ウイルス感染（風疹等）、薬剤、放射線照射などがあります。水溶性ビタミンである「ビタミンC」は奇形の直接的原因とはなりません。"),
    (65, "虫垂 － 右下腹部", "select_correct", False,
     "虫垂および回盲部は右腸骨窩（「右下腹部」）に位置し、マックバーネー点やランツ点として圧痛を触知します。肝臓は右季肋部、脾臓は左季肋部、膵臓は上腹部深部に位置します。"),
    (66, "触診法では最低血圧はわからない。", "select_correct", False,
     "マンシェットを加圧後に減圧しながら橈骨動脈の拍動を触知する触診法では、血流が再開する収縮期血圧（最高血圧）のみが測定可能であり、拡張期血圧（最低血圧）は測定できません。"),
    (67, "タバコ", "select_correct", False,
     "お茶やコーヒー（カフェインによる利尿作用）、アルコール（抗利尿ホルモン分泌抑制による利尿作用）は多尿の原因となります。タバコ（ニコチン）は抗利尿ホルモン（ADH）分泌を刺激してむしろ尿量を減少させる方向に働きます。"),
    (68, "筋電図 － 心筋症", "select_incorrect", False,
     "筋電図（EMG）は骨格筋や末梢神経疾患の検査です。心筋症や不整脈などの心疾患の診断には「心電図（ECG）」や心エコー検査が用いられます。"),
    (69, "肩関節", "select_correct", False,
     "球関節（多軸関節）に分類されるのは「肩関節（肩甲上腕関節）」および股関節（臼状関節）です。肘関節（腕尺関節）や膝関節は蝶番関節（または顆状関節）、足関節は距腿関節（蝶番関節）です。"),
    (70, "膝蓋腱反射", "select_correct", False,
     "「膝蓋腱反射」は健常人において普遍的にみられる生理的な深部腱反射（伸張反射）です。バビンスキー反射、チャドック反射、オッペンハイム反射は錐体路障害時に現れる病的反射です。"),
    (71, "狭心症", "select_correct", False,
     "狭心症は一時的な心筋虚血による胸痛発作であり、通常は意識清明です。低血糖（低血糖昏睡）、てんかん発作、各種ショック（循環虚脱）は脳血流や脳代謝の低下により直接意識障害を引き起こします。"),
    (72, "腰神経", "select_correct", False,
     "大腿四頭筋は腰神経叢（L2〜L4）から起こる「大腿神経」によって支配されるため、腰神経（腰髄）の障害によって麻痺が生じます。"),
    (73, "聴覚", "select_correct", False,
     "「聴覚」は内耳のラセン器で受容される特殊感覚です。皮膚や粘膜で受容される触覚、圧覚、痛覚、温度覚（温覚・冷覚）が「表在感覚（体性感覚）」に分類されます。"),
    (74, "交感神経は血管に分布する。", "select_correct", False,
     "交感神経節後線維は全身の血管壁平滑筋に広く分布し、血管運動神経（血管収縮）として血圧や血流の調節を担います。骨格筋は体性運動神経支配であり、自覚的求心情報は知覚神経（求心性）が伝えます。"),
    (75, "腋窩神経", "select_correct", False,
     "肩関節の外転・屈曲・伸展を担う三角筋は、腕神経叢後索から起こる「腋窩神経（C5・C6）」によって支配されます。")
]

part4_answers = [
    (76, "Ｓ1，2", "select_correct", False,
     "アキレス腱反射（足陰腱反射）の反射中枢は「仙髄第1〜第2分節（S1・S2）」です（脛骨神経・坐骨神経を介する）。膝蓋腱反射の中枢はL2〜L4です。"),
    (77, "飲酒", "select_correct", False,
     "肺気腫（慢性閉塞性肺疾患：COPD）の最大の原因は「喫煙」であり、その他大気汚染や慢性気管支炎などが関与します。適度な飲酒単独が肺気腫の直接的原因とはなりません。"),
    (78, "吐血", "select_correct", False,
     "肺癌の主な呼吸器症状は血痰・喀血、咳嗽、胸痛、嗄声（反回神経麻痺）、呼吸困難、胸膜浸潤による胸水貯留などです。「吐血」は食道・胃・十二指腸などの上部消化管からの出血症状です。"),
    (79, "リウマチ熱", "select_correct", False,
     "A群β溶血性連鎖球菌感染後に発症する自己免疫疾患である「リウマチ熱」は、心内膜炎や弁尖の癒着・変形を引き起こし、後天性心臓弁膜症（僧帽弁狭窄症・閉鎖不全症等）の主要な原因となります。"),
    (80, "鉄分", "select_correct", False,
     "ヘモグロビンの構成成分である「鉄分」が不足すると、小球性低色素性貧血である「鉄欠乏性貧血」を発症します。"),
    (81, "血糖値が下降する。", "select_incorrect", False,
     "糖尿病の本態はインスリンの作用不足による「慢性的な血糖値の上昇（高血糖）」です。口渇、多飲、多尿、体重減少、易感染性などを呈します。"),
    (82, "尿酸の低下", "select_incorrect", False,
     "痛風の本態はプリン体代謝異常による「高尿酸血症（血清尿酸値の上昇）」です。尿酸塩結晶が関節内に析出して激痛を伴う急性関節炎（第1中足趾節関節等）や尿路結石、痛風腎を引き起こします。"),
    (83, "粘液水腫 － 活動性の亢進", "select_incorrect", False,
     "粘液水腫（甲状腺機能低下症）では基礎代謝が低下するため、無気力、活動性の低下（倦怠感・動作緩慢）、徐脈、寒がり、浮腫などを呈します。活動性の亢進は甲状腺機能亢進症の症状です。"),
    (84, "意識障害", "select_incorrect", False,
     "重症筋無力症（MG）は神経筋接合部のアセチルコリン受容体に対する自己抗体による疾患であり、筋力低下・易疲労性（眼瞼下垂、複視、構音・嚥下困難、呼吸筋麻痺等）を呈しますが、中枢神経系は侵されないため「意識障害」はみられません。"),
    (85, "坐骨神経痛 － 大腿前面", "select_incorrect", False,
     "坐骨神経痛の疼痛・しびれは、坐骨神経の走行に沿って「臀部から大腿後面、下腿外側・後面、足部」に出現します。大腿前面の疼痛は大腿神経痛（大腿神経障害）の特徴です。"),
    (86, "ビタミンＥ － くる病", "select_incorrect", False,
     "くる病（小児）および骨軟化症（成人）は「ビタミンD」の欠乏によってカルシウム吸収が障害されて起こる骨疾患です。"),
    (87, "脛骨下部", "select_correct", False,
     "ランニングやジャンプ動作の繰り返しにより発生する疲労骨折の好発部位は「脛骨（下1/3〜中下1/3部）」、「中足骨（第2・3中足骨）」、「腓骨」などです。"),
    (88, "脊柱管狭窄症", "select_correct", False,
     "歩行に伴い下肢痛やしびれが出現し、前屈姿勢やしゃがんで休むと軽快・消失する症状は「神経性間欠性跛行」であり、腰部「脊柱管狭窄症」の典型的臨床像です。"),
    (89, "慢性肝炎 － チアノーゼ", "select_incorrect", False,
     "チアノーゼは動脈血中の還元ヘモグロビン濃度の増加（呼吸不全や右左シャントを伴う心疾患等）によって皮膚・粘膜が青紫色になる所見です。慢性肝炎の主徴候は倦怠感や黄疸、肝機能異常等です。"),
    (90, "大腸ポリープ", "select_correct", False,
     "胸やけ（胃食道逆流症状）は胃酸の上皮逆流によって起こるため、胃食道逆流症、食道炎、食道裂孔ヘルニア、胃炎、神経症などでみられます。下部消化管疾患である「大腸ポリープ」は胸やけの原因とはなりません。"),
    (91, "潰瘍性大腸炎", "select_correct", False,
     "「潰瘍性大腸炎」は大腸粘膜の炎症・潰瘍性病変であり、主症状は粘血便や下血（血便）です。口から胃内容物・血液を吐き出す「吐血」の原因には上部消化管疾患（胃潰瘍、十二指腸潰瘍、食道静脈瘤、胃癌等）が挙げられます。"),
    (92, "血尿", "select_correct", False,
     "肝硬変では門脈圧亢進や肝機能低下（エストロゲン代謝不全等）により、くも状血管腫、手掌紅斑、女性化乳房、脾腫、腹水、食道静脈瘤などが現れます。通常「血尿」は腎・尿路系疾患の症候であり、肝硬変の典型的症状ではありません。"),
    (93, "足関節の伸展ができない。", "select_correct", False,
     "総腓骨神経（深腓骨神経）麻痺では、前脛骨筋や長趾伸筋の麻痺により足関節の背屈（伸展）および足趾の伸展が不能となり、尖足（下垂足）および鶏歩を呈します。"),
    (94, "糖尿病", "select_correct", False,
     "「糖尿病」の三大合併症の一つである糖尿病性神経障害では、高血糖に伴う代謝障害と微小血管障害により、両側四肢末梢（手袋靴下型）に対称性の末梢神経炎・知覚鈍麻・しびれをきたします。"),
    (95, "ツベルクリン反応が陽転する。", "select_incorrect", False,
     "ツベルクリン反応が陽転するのは「結核」感染時です。リウマチ熱はA群連鎖球菌感染後の非化膿性炎症疾患であり、多発性関節炎、心内膜炎、舞踏病、輪状紅斑、皮下結節などを呈します。"),
    (96, "低血圧", "select_incorrect", False,
     "慢性糸球体腎炎では腎機能障害や水・ナトリウム貯留、レニン分泌異常に伴い「高血圧」を合併するのが典型的です。低血圧となることは通常ありません。"),
    (97, "期外収縮が 1 分間 20 以上", "select_correct", False,
     "運動療法中に頻発する期外収縮（1分間に20回以上、または多源性・連発性期外収縮）が出現した場合は、心室細動などの致死性不整脈へ移行する危険性が極めて高いため、直ちに運動を「中止」すべき絶対的中止基準です。"),
    (98, "パーキンソン病 － すくみ足歩行", "select_correct", False,
     "パーキンソン病では無動・寡動、筋強剛、姿勢反射障害により、歩行開始困難（すくみ足）、小刻み歩行、前傾姿勢、突進現象（加速歩行）などが特徴的にみられます。"),
    (99, "急性期から積極的に腰痛体操を行う。", "select_incorrect", False,
     "激しい疼痛や炎症を伴う急性期の腰痛症に対しては、まず安静や局所の免荷が最優先されます。急性期からの積極的な腰痛体操は症状を悪化させる危険があるため誤りです。"),
    (100, "立脚中期とは全体重がその足に乗っている時をいう。", "select_correct", False,
     "歩行周期の立脚相において、「立脚中期（Mid-stance）」とは反対側の足が地面から離れて遊脚期となり、全体重が支持側の単一下肢に乗っている期間を指します。")
]

if __name__ == "__main__":
    create_patches_for_part(3, part3_answers)
    create_patches_for_part(4, part4_answers)
