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


part3_answers = [
    (51, "腱紡錘", "select_correct", False,
     "腱の中に存在し、筋の収縮によって腱にかかる張力（引っ張り力）の大きさを感知してIb求心性線維を介して中枢へ伝える受容器は「腱紡錘（ゴルジ腱器官）」です。筋紡錘は筋の長さを感知します。"),
    (52, "アセチルコリン", "select_correct", False,
     "副交感神経節後線維終末から分泌され、心拍数減少、消化管運動亢進、縮瞳などを引き起こす神経伝達物質は「アセチルコリン」です。"),
    (53, "網膜", "select_correct", False,
     "光刺激を受容する視細胞（杆体細胞・錐体細胞）が存在する感覚受容組織は眼球最内層の「網膜」です。"),
    (54, "コリンエステラーゼ", "select_correct", False,
     "シナプス間隙に放出されたアセチルコリンをコリンと酢酸に速やかに加水分解して作用を消失させる酵素は「コリンエステラーゼ（アセチルコリンエステラーゼ）」です。"),
    (55, "副腎髄質", "select_correct", False,
     "交感神経節前線維（コリン作動性）の直接支配を受け、刺激に応じて血中にアドレナリンやノルアドレナリンを分泌する内分泌器官は「副腎髄質」です。"),
    (56, "肺動脈塞栓症", "select_correct", False,
     "下肢の深部静脈等で形成された血栓が血流に乗って遊離し、右心房・右心室を経て肺動脈を閉塞する致死的な病態は「肺動脈塞栓症（肺血栓塞栓症）」です。"),
    (57, "ネクローシス", "select_correct", False,
     "生体内の局所組織や細胞が不可逆的な損傷を受けて死滅することを「壊死（ネクローシス：Necrosis）」と呼びます。"),
    (58, "壊死", "select_correct", False,
     "組織への動脈血流が急性または完全に遮断された結果、その支配領域の組織が不可逆的な虚血に陥って死滅する病態は「壊死（梗塞巣の壊死）」です。"),
    (59, "血管透過性の亢進", "select_correct", False,
     "急性炎症の初期反応において、ヒスタミン等の化学伝達物質の作用により細小血管の「血管透過性が亢進」し、血漿蛋白質や水分が血管外へ漏出して局所の浮腫・腫脹が生じます。"),
    (60, "肉芽組織", "select_correct", False,
     "創傷や組織欠損の修復過程において、毛細血管の新生と線維芽細胞の増殖によって形成されるみずみずしい幼若な結合組織は「肉芽組織」です。"),
    (61, "慢性関節リウマチ", "select_correct", False,
     "「慢性関節リウマチ（関節リウマチ）」は関節滑膜に対する自己免疫応答により滑膜炎や骨・軟骨破壊が進行する代表的な膠原病（自己免疫疾患）です。"),
    (62, "神経細胞", "select_correct", False,
     "中枢神経系の「神経細胞（ニューロン）」は高度に分化しており、出生後は細胞分裂・増殖能を持たない（永久細胞）ため、一度破壊・脱落すると再生しません。線維芽細胞や骨細胞、内皮細胞は再生能を有します。"),
    (63, "腺腫", "select_correct", False,
     "腺上皮から発生する良性の上皮性腫瘍は「腺腫（アデノーマ）」です。血管腫、脂肪腫、平滑筋腫は間葉系組織から発生する非上皮性腫瘍です。"),
    (64, "エイズ", "select_correct", False,
     "エイズ（後天性免疫不全症候群）はヒト免疫不全ウイルス（HIV：レトロウイルス）の感染によってヘルパーTリンパ球が破壊されて発症するウイルス感染症です。"),
    (65, "ネフローゼ症候群", "select_incorrect", False,
     "頻尿（排尿回数の異常増加）は膀胱炎、尿道炎、尿路結石、前立腺肥大症などでみられます。ネフローゼ症候群では高度の蛋白尿と低蛋白血症により体内に水分が貯留するため、通常は「乏尿」を呈します。"),
    (66, "黄疸", "select_correct", False,
     "肝硬変では肝細胞障害および肝内胆汁うっ滞に伴い血中ビリルビン値が上昇し、皮膚や眼球結膜が黄色に染まる「黄疸」を呈します。"),
    (67, "左季肋部", "select_correct", False,
     "脾臓は左側腹部・背側の第9〜第11肋骨下に位置するため、脾腫（脾臓の腫大）がある場合は「左季肋部」に触知されます。"),
    (68, "食事", "select_incorrect", False,
     "健康な状態での通常の「食事」によって尿中に明らかな蛋白質が漏出することはありません。発熱、起立性蛋白尿（腎下垂等）、過激な運動（運動性蛋白尿）は生理的・機能的蛋白尿の原因となります。"),
    (69, "上前腸骨棘から脛骨内果部まで", "select_correct", False,
     "臨床医学における真の下肢長（棘果長：SMD）の計測点は、「上前腸骨棘（ASIS）」から「脛骨内果下端」までです。"),
    (70, "肘", "select_correct", False,
     "前腕の回内・回外運動は、上橈尺関節および下橈尺関節（「肘関節および手関節近傍」）の協調運動によって行われます。"),
    (71, "神経線維の断裂", "select_correct", False,
     "「筋電図検査（針筋電図・神経伝導速度検査）」は、末梢神経損傷（神経線維断裂や脱髄）に伴う脱神経電位（自発電位）や神経筋接合部異常の診断に極めて有用です。"),
    (72, "オージオメーター検査", "select_correct", False,
     "種々の周波数における純音の最小可聴閾値を測定し、難聴の程度や性質（伝音難聴・感音難聴）を検査する機器・方法は「オージオメーター検査（純音聴力検査）」です。"),
    (73, "排便", "select_correct", False,
     "生体が生きている状態を示す基本的指標である生命徴候（バイタルサイン）は「体温」「脈拍（心拍）」「血圧」「呼吸」および「意識状態」の5つです。「排便」は排泄機能の観察項目でありバイタルサインには含まれません。"),
    (74, "肺動脈には動脈血が流れる。", "select_incorrect", False,
     "「肺動脈」は右心室から肺へと全身を循環した酸素の少ない「静脈血」を送り出す血管です。肺毛細血管でガス交換された酸素に富む動脈血は「肺静脈」を通って左心房へ戻ります。"),
    (75, "最高血圧と最低血圧との差を脈圧という", "select_correct", False,
     "収縮期血圧（最高血圧）と拡張期血圧（最低血圧）との差を「脈圧」と呼びます。最低血圧は動脈の拡張期圧であり、最高血圧は心室収縮期の動脈圧です。")
]

part4_answers = [
    (76, "三叉神経痛", "select_incorrect", False,
     "めまい（眩暈）は内耳迷路疾患（メニエール病等）、前庭神経炎、脳幹・小脳疾患（中枢性）など前庭神経系・小脳の障害で生じます。三叉神経痛は顔面の鋭い発作性疼痛疾患であり、めまいの直接原因とはなりません。"),
    (77, "血尿、疼痛がある。", "select_correct", False,
     "尿路結石症（腎結石・尿管結石）の典型的症状は、腰背部から側腹部・下腹部にかけての激しい疝痛発作と肉眼的または顕微鏡的「血尿」です。"),
    (78, "中・小殿筋", "select_correct", False,
     "歩行時に患側下肢で荷重した際、股関節外転筋である「中殿筋・小殿筋（上殿神経支配）」の筋力低下により骨盤の水平が保てず、健側骨盤が沈下する歩行異常を「トレンデレンブルグ歩行」と呼びます。"),
    (79, "腫瘤は無痛性である。", "select_correct", False,
     "「乳癌」の典型的な初期所見は、乳房に触知される「無痛性で硬く、可動性の乏しい（境界不明瞭な）腫瘤」です。女性ホルモン環境（未経産・高齢初産等）が危険因子とされます。"),
    (80, "石綿(アスベスト)は肺癌の原因となる", "select_correct", False,
     "アスベスト（石綿）の吸入曝露は、悪性胸膜中皮腫および「肺癌」の重大な発症原因（職業病・環境病）となります。"),
    (81, "非遺伝性である。", "select_incorrect", False,
     "本態性高血圧症は遺伝的素因（家族歴）と環境要因（塩分過剰摂取、肥満、ストレス、運動不足等）が複合して発症する多因子疾患であり、「非遺伝性である」とする記述は誤りです。"),
    (82, "Ｈ型", "select_incorrect", False,
     "ウイルス性肝炎の原因ウイルスとして同定・分類されているのはA型、B型、C型、D型、E型等です。「H型肝炎」という分類は存在しません。"),
    (83, "椎間孔が拡大する。", "select_incorrect", False,
     "腰椎椎間板ヘルニアでは、髄核が線維輪を破って後側方へ脱出し、椎間孔や脊柱管を狭窄させて神経根を圧迫します。椎間孔が拡大することはありません。"),
    (84, "ストレスが原因となる。", "select_correct", False,
     "消化性潰瘍（胃潰瘍・十二指腸潰瘍）の成因には、ピロリ菌感染、NSAIDsの服用のほか、精神的・肉体的「ストレス」による自律神経失調と胃酸分泌過多・粘膜防御能低下が深く関与します。"),
    (85, "脊椎分離症 ― 卓球", "select_incorrect", False,
     "脊椎分離症（腰椎分離症）は成長期のジャンプや腰椎の回旋・過伸展を繰り返すスポーツ（野球、サッカー、体操、バレーボール等）に多発する疲労骨折です。卓球は直接の代表的受傷機転の組合せとしては不適切です。"),
    (86, "第 4－5 腰椎間椎間板ヘルニア", "select_correct", False,
     "ラセーグ試験（下肢伸展挙上テスト：SLRテスト）は坐骨神経（L4〜S2）の牽引徴候を調べる検査であり、「第4-5腰椎間（L5神経根）」または第5腰椎-第1仙椎間椎間板ヘルニアで陽性となります。"),
    (87, "予後不良である。", "select_incorrect", False,
     "いわゆる五十肩（肩関節周囲炎）は自然治癒傾向（自己限定性）があり、急性期・拘縮期を経て適切な保存的治療・運動療法を行うことで多くは半年〜1年程度で寛解・治癒するため「予後は良好」です。"),
    (88, "湿疹", "select_correct", False,
     "「湿疹（皮膚炎）」はアレルギー反応や物理化学的刺激に対する皮膚の表在性炎症反応であり、細菌感染症ではありません。蜂巣炎、ひょう疽、癰（よう）は化膿菌による細菌性感染症です。"),
    (89, "耳漏", "select_correct", False,
     "メニエール病の主徴候は内リンパ水腫による「発作性回転性めまい」「難聴」「耳鳴」「耳閉塞感」の4主徴です。外耳炎や中耳炎でみられる「耳漏（耳だれ）」はみられません。"),
    (90, "左片麻痺", "select_correct", False,
     "大脳半球の運動野や内包を通る錐体路は延髄で対側へ交叉（錐体交叉）するため、右側大脳半球の脳内出血では対側である「左側の片麻痺（左片麻痺）」が生じます。"),
    (91, "吸気時の呼吸困難が強い。", "select_incorrect", False,
     "気管支喘息は気道攣縮と粘膜浮腫・粘液分泌亢進により末梢気道が狭窄するため、「呼気性の呼吸困難（呼気延長・喘鳴）」が強く現れる閉塞性換気障害です。"),
    (92, "心房中隔欠損症", "select_correct", False,
     "「心房中隔欠損症（ASD）」は心房中隔の形成不全により左右の心房間に短絡が生じる代表的な先天性心疾患です。"),
    (93, "予後は良好である。", "select_incorrect", False,
     "慢性白血病（慢性骨髄性白血病・慢性リンパ性白血病）は血液の悪性腫瘍であり、進行すると急性転化（ブラストクリーゼ）を起こし重篤な経過をたどるため、「予後は良好である」とする記述は誤りです。"),
    (94, "精神分裂病に比べて予後不良である", "select_incorrect", False,
     "躁うつ病（双極性障害・うつ病）は発作性・間欠性の経過をたどり、病相期を脱すれば正常な人格状態に回復するため、人格荒廃を招きやすい精神分裂病（統合失調症）と比較して「予後は比較的良好」とされます。"),
    (95, "インスリン過剰によって起こる。", "select_incorrect", False,
     "糖尿病の本態はインスリン分泌不足またはインスリン抵抗性による「インスリン作用の不足」です。インスリン過剰は低血糖症を引き起こします。"),
    (96, "血中コレステロール値が上昇する。", "select_incorrect", False,
     "慢性関節リウマチの検査所見としては、赤沈亢進、CRP陽性、リウマトイド因子（RF）陽性、正球性正色素性貧血などがみられます。脂質異常症（高コレステロール血症）は直接の所見ではありません。"),
    (97, "単シナプス反射は一つのニューロンによって成り立っている。", "select_incorrect", False,
     "単シナプス反射（伸張反射・腱反射）は、求心性ニューロン（感覚神経）と遠心性ニューロン（運動神経）の「2つのニューロン」が1つのシナプスで結合して成立します。1つのニューロンのみで成り立つわけではありません。"),
    (98, "大胸筋により外転する。", "select_incorrect", False,
     "大胸筋の主な作用は肩関節の「屈曲・内転・内旋」です。肩関節の外転は三角筋（中部線維）および棘上筋が担います。"),
    (99, "関節周辺の筋力低下が起こる。", "select_correct", False,
     "変形性関節症では関節軟骨の摩耗に伴う疼痛や可動域制限のために関節の使用が控えられ、関節周囲筋（大腿四頭筋等）の「廃用性筋力低下・萎縮」が生じます。"),
    (100, "急性再燃することがある。", "select_correct", False,
     "慢性関節リウマチは寛解と増悪（急性再燃）を繰り返しながら徐々に多発性関節破壊や関節変形が進行する慢性炎症性疾患です。")
]

if __name__ == "__main__":
    create_patches_for_part(3, part3_answers)
    create_patches_for_part(4, part4_answers)
