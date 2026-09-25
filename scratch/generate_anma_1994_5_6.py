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


part5_answers = [
    (101, "心", "select_correct", False,
     "東洋医学において、血脈をめぐらし精神活動・意識をつかさどる（君主の官）のは「心」です。"),
    (102, "気逆", "select_correct", False,
     "気の昇降運動が乱れ、気が下降せずに上方へ衝き上がる病態を「気逆」と呼び、咳嗽、喘息、しゃっくり（吃逆）、嘔吐、頭痛・のぼせなどを引き起こします。"),
    (103, "胆", "select_correct", False,
     "五臓六腑の中で決断をつかさどり「中正の官」とされるのは「胆」です。"),
    (104, "涙", "select_correct", False,
     "五液の配当において「肝は涙」「心は汗」「脾は涎」「肺は涕」「腎は唾」をつかさどります。したがって肝に対応する体液は「涙」です。"),
    (105, "皮毛", "select_correct", False,
     "五体の配当において「肺は皮毛」「肝は筋」「心は脈」「脾は肉」「腎は骨」をつかさどります。したがって肺が主るのは「皮毛」です。"),
    (106, "陽虚", "select_correct", False,
     "陽気が不足して温煦作用が低下した状態を「陽虚」といい、畏寒（寒がり）、四肢の冷え、自汗、自覚的な冷え感を呈します。"),
    (107, "腎", "select_correct", False,
     "五行配当において「水」に属する五臓は「腎」です。"),
    (108, "脾", "select_correct", False,
     "五行配当において「甘」の味（五味）に対応する五臓は「脾」です。"),
    (109, "肝虚", "select_correct", False,
     "五行相生理論に基づき、「虚すればその母を補う」原則により、肝（木）の虚証に対しては母である水（腎）の経穴を補います。"),
    (110, "血熱", "select_correct", False,
     "熱邪が血分に侵入し血行を過剰に亢進させて脈外に溢れ出させる病態を「血熱」といい、喀血、吐血、鼻出血、皮下出血などの出血症状を引き起こします。"),
    (111, "燥邪", "select_correct", False,
     "六淫のうち、乾燥を特徴とし津液を損傷しやすく肺を傷めやすい病邪は「燥邪」です。"),
    (112, "悲", "select_correct", False,
     "七情のうち、過度の「悲（または憂）」は気の消沈・消耗をもたらし、「悲しめば則ち気消す」とされます。"),
    (113, "猛暑の中でテニスをした。", "select_correct", False,
     "「暑邪」は夏季の炎熱・猛暑の気によって発症する外邪であり、酷暑の環境下での激しい活動が直接の誘因となります。"),
    (114, "聞診 － 脈状", "select_incorrect", False,
     "脈状を診察するのは患者の脈に触れる「切診」です。聞診は患者の声・呼吸音・咳や体臭などを聴覚・嗅覚で診察する方法であり、「聞診 － 脈状」は誤りです。"),
    (115, "手の陽明大腸経", "select_correct", False,
     "示指橈側端（商陽）に始まり、上肢外側前縁、肩前部、側頸部を経て鼻翼外方の迎香に終わるのは「手の陽明大腸経」です。"),
    (116, "3 本", "select_correct", False,
     "下腿内側を上行する足の三陰経は、足の太陰脾経、足の厥陰肝経、足の少陰腎経の「3本」です。"),
    (117, "陰谷", "select_correct", False,
     "足の少陰腎経の合水穴は膝窩横紋内端（半腱様筋腱と半膜様筋腱の間）にある「陰谷穴」です。"),
    (118, "神門", "select_correct", False,
     "手関節掌側横紋上、尺側手根屈筋腱の橈側縁にある手の少陰心経の原穴・兪土穴は「神門穴」です。"),
    (119, "膏肓は第 4 胸椎棘突起の下の外方 3 寸に取る。", "select_correct", False,
     "膏肓穴は「第4胸椎棘突起下縁の外方3寸」に取穴します。"),
    (120, "腎兪", "select_correct", False,
     "第2腰椎棘突起下縁の外方1寸5分に位置する足の太陽膀胱経の経穴は「腎兪穴」です。大腸兪は第4腰椎、小腸兪は第1仙骨孔、肝兪は第9胸椎レベルです。"),
    (121, "大椎", "select_correct", False,
     "第7頸椎棘突起下縁の陥凹部に位置する経穴は「大椎穴」であり、督脈に属します。天枢は胃経、膻中および中脘は任脈です。"),
    (122, "梁門", "select_correct", False,
     "「梁門穴」は足の陽明胃経に属する経穴（上腹部、臍上4寸外方2寸）であり、募穴ではありません。中脘は胃の募穴、日月は胆の募穴、石門は三焦の募穴です。"),
    (123, "足の陽明胃経", "select_correct", False,
     "鼻翼外方（迎香）の脈気を受けて眼窩下（承泣）に起こり、頸部・胸部・腹部・大腿前外側・下腿前外側を下って足の第2指外側端（厲兌）に終わるのは「足の陽明胃経」です。"),
    (124, "正経 12、奇経 8", "select_correct", False,
     "古典経絡体系において、臓腑に直接配当された「正経（十二経脈）」は12本、正経を統括・調整する「奇経（奇経八脈）」は8本存在します。"),
    (125, "擦過軽擦はあん摩の按撫法（軽擦法）の一種である。", "select_incorrect", False,
     "擦過軽擦法（ストローキング）はヨーロッパ発祥の「マッサージ手技（軽擦法）」の一種です。伝統的な日本のあん摩の手技（按撫法）ではありません。")
]

part6_answers = [
    (126, "緩圧法", "select_correct", False,
     "指圧の押圧法において、圧を急激に加えず徐々に深く押し込んでいき（二段押し・三段押し）、深部組織にまで刺激を到達させる手技は「緩圧法」です。"),
    (127, "アルントシュルツ", "select_correct", False,
     "「微弱な刺激は神経機能を喚起し、中等度の刺激はこれを興奮させ、強度の刺激はこれを抑制し、最強度の刺激はこれを静止させる」という生体反応の法則を提唱したのは「アルント・シュルツ（Arndt-Schulz）」です。"),
    (128, "感覚の投射性が関与する。", "select_correct", False,
     "内臓疾患による侵害刺激が求心性自律神経（内臓感覚神経）を介して脊髄後角に入力した際、同じ脊髄分節の体性感覚神経からの入力と混同・誤認される「感覚の投射（投射痛・関連痛・連関痛）」が生じます。"),
    (129, "後索路", "select_correct", False,
     "精細な識別性触圧覚および深部感覚（位置覚・振動覚）を上位中枢（延髄の後索核・薄束核・楔状束核を経て視床・大脳皮質）へと伝える上行性伝導路は「後索路（後索・内側毛帯路）」です。"),
    (130, "矯正法", "select_correct", False,
     "徒手的な力や他動運動を加えて関節拘縮や筋短縮を緩和し、正常な関節可動域および骨関節配列を回復させる手技手腕は「矯正法（関節矯正法）」です。"),
    (131, "触圧刺激", "select_correct", False,
     "ゲートコントロール説では、大径の有髄線維（Aβ線維）が伝える「触圧刺激」が脊髄後角膠様質細胞を興奮させ、小径線維（Aδ・C線維）が伝える痛覚伝達のシナプス前抑制（ゲート閉鎖）を引き起こすと説明されます。"),
    (132, "鎮静作用", "select_correct", False,
     "手技療法において、強い持続的圧迫刺激や重圧法などの強刺激を加えることにより、過剰に興奮した神経機能や筋緊張・痙縮を抑える作用を「鎮静作用（抑制作用）」と呼びます。"),
    (133, "消化機能を抑制する。", "select_incorrect", False,
     "マッサージなどの手技療法は、副交感神経機能を高めて消化管の蠕動運動や消化液分泌を促進し、胃腸機能を正常化（亢進）させます。「消化機能を抑制する」とする記述は不適切です。"),
    (134, "キャノン", "select_correct", False,
     "生体の内部環境が一定に維持される恒常性の仕組みを「ホメオスタシス（Homeostasis）」と命名し提唱したアメリカの生理学者は「キャノン（W.B.Cannon）」です（概念の基礎はベルナールが提唱）。"),
    (135, "顔面神経麻痺 － 顔面全体のマッサージ", "select_correct", False,
     "同側の額のしわ寄せ不能、閉眼不能（兎眼）、口笛不能（口角下垂）は「末梢性顔面神経麻痺（ベル麻痺等）」の典型像であり、麻痺側を中心とした顔面全体の愛護的マッサージが適応となります。"),
    (136, "ローゼンタール法", "select_correct", False,
     "VDT作業による眼精疲労に対しては、頸肩部の筋緊張緩和（揉捏法、ネーゲリーの伸頭法）や眼窩周囲の指圧・圧迫法が有効です。「ローゼンタール法」は別の特殊法であり眼精疲労の直接手技ではありません。"),
    (137, "更年期障害", "select_correct", False,
     "更年期障害に伴う自律神経失調性のめまい、全身倦怠感、肩こり、冷え・のぼせなどに対しては、全身的なマッサージによる自律神経調整・循環改善が極めて有効です。脳動脈硬化や急性内耳疾患は専門医による治療が優先されます。"),
    (138, "片側でワレー圧痛点が認められる痛み。", "select_correct", False,
     "肋間神経の走行に沿った片側の鋭い疼痛で、肋間神経の出口（脊柱傍・側胸部・胸骨傍）にワレー圧痛点を認める「肋間神経痛」は手技療法の好適応症です。狭心症疑いの絞扼感や気胸・胸膜炎疑いの突然の胸痛・呼吸困難は禁忌です。"),
    (139, "線維成分の少ない食事をとらせる。", "select_incorrect", False,
     "弛緩性便秘（数日に1回、太く長い便を出す）に対しては、大腸の蠕動運動を刺激するため「食物繊維や水分に富む食事」の摂取が推奨されます。線維の少ない食事を指導するのは誤りです。"),
    (140, "稀発月経の場合", "select_correct", False,
     "周期が39日以上3ヶ月未満と長い機能性の「稀発月経」や自律神経失調に伴う月経不順は、全身調整や腰仙部・腹部への手技療法の適応度が高い病態です。不正出血や急激に増強する月経痛は器質的疾患（子宮筋腫・子宮内膜症等）が疑われます。"),
    (141, "頸部を強く後屈して牽引を行う。", "select_incorrect", False,
     "頸部神経根症状（上肢の放散痛・しびれ）を有する患者に対し、頸部を強く後屈させると椎間孔が狭小化して神経根の圧迫が増悪（ジャクソンテスト・スパーリングテストの肢位）し、症状を悪化させるため絶対に行ってはなりません。"),
    (142, "尺骨神経痛 － 手の太陰肺経", "select_incorrect", False,
     "尺骨神経は前腕・上肢の内側後面を走行するため、東洋医学的には「手の少陰心経」および「手の太陽小腸経」が治療対象となります。「手の太陰肺経」は前外側を走行し橈骨神経・筋皮神経領域に相当します。"),
    (143, "手関節に抵抗を加えながら背屈運動をさせる。", "select_incorrect", False,
     "テニス肘（上腕骨外側上顆炎）の患者に手関節の抵抗下背屈運動を行わせると、短橈側手根伸筋腱の起始部に強い牽引ストレスが加わり炎症を悪化（トムゼンテスト陽性肢位）させるため、急性期・有痛期の運動負荷としては不適切です。"),
    (144, "大腿伸筋の伸展法", "select_incorrect", False,
     "腰部から右下肢後面（坐骨神経領域）にかけての疼痛を有する坐骨神経痛患者に対して、下肢後面の伸筋群（ハムストリングス）や坐骨神経を過度に強く伸展（SLR様肢位での無理なストレッチ）させると神経根刺激を悪化させるため避けるべきです。"),
    (145, "大腿四頭筋", "select_correct", False,
     "変形性膝関節症において、膝関節の力学的安定性を高め関節軟骨への荷重負担を軽減するために最も重要な筋力強化対象は「大腿四頭筋（特に内側広筋）」です。"),
    (146, "前胸部の叩打法", "select_incorrect", False,
     "本態性高血圧症に対しては、末梢血管を拡張させ副交感神経を優位にする愛護的な軽擦法・揉捏法・按腹法が適応となります。交感神経を刺激し急激な血圧上昇や不整脈を誘発する恐れのある「前胸部への叩打法」は不適切（禁忌）です。"),
    (147, "温める。", "select_incorrect", False,
     "スポーツ外傷の急性期応急処置は「RICE処置（Rest：安静、Icing：冷却、Compression：圧迫、Elevation：挙上）」が鉄則です。急性炎症期に局所を「温める（温熱）」と内出血や腫脹が増悪するため誤りです。"),
    (148, "訴えが多様なときは強い刺激で行う。", "select_incorrect", False,
     "高齢者は予備能や生体反応が低下しており、不定愁訴や多様な訴えに対して強刺激を加えると、自律神経の乱れ（オーバースティミュレーション）や筋・骨損傷を招く危険があります。刺激量は「軽微・適量」に留めるべきです。"),
    (149, "肩関節周囲炎", "select_correct", False,
     "50歳代の女性にみられる、結髪動作（髪をとかす）や結帯動作（エプロンの紐を結ぶ）、挙上動作時の肩関節痛および運動制限は、いわゆる五十肩である「肩関節周囲炎」の典型的症状です。"),
    (150, "罹患部にホットパックを施す。", "select_correct", False,
     "肩関節周囲炎（慢性期・拘縮期）の治療として、局所の血流促進と筋・腱・関節包の柔軟性改善を目的とした「温熱療法（ホットパック）」や愛護的手技療法は極めて有効です。")
]

if __name__ == "__main__":
    create_patches_for_part(5, part5_answers)
    create_patches_for_part(6, part6_answers)
