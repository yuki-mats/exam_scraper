# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2005 question_2005_3.json (Q51 - Q75)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2005/00_source/question_2005_3.json"

P10_DIR = BASE_DIR / "output/shinkyu/questions_json/2005/10_questionType_fixed"
P15_DIR = BASE_DIR / "output/shinkyu/questions_json/2005/15_correctChoiceText_fixed"
P23_DIR = BASE_DIR / "output/shinkyu/questions_json/2005/23_correctChoiceText_fixed"
P21_DIR = BASE_DIR / "output/shinkyu/questions_json/2005/21_explanationText_added"

for p in [P10_DIR, P15_DIR, P23_DIR, P21_DIR]:
    p.mkdir(parents=True, exist_ok=True)

with open(SOURCE_FILE, "r", encoding="utf-8") as f:
    source_data = json.load(f)
    source_questions = source_data.get("question_bodies", source_data)

questions_data = [
    {
        "qid": "7019a33ceef91bde",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。ポリオウイルスは脊髄前角運動細胞に感染して急性灰白髄炎（弛緩性麻痺）を引き起こします。",
            "正しい。水痘・帯状疱疹ウイルスは知覚神経節に潜伏感染し、再活性化時に末梢神経に沿って水疱と激痛を生じます。",
            "正しい。結核菌は飛沫核感染により主として肺に初感染病巣を形成します。",
            "間違い。赤痢菌（*Shigella*）は「大腸（結腸）」の粘膜上皮に侵入して壊死・潰瘍を形成し、膿粘血便を引き起こします。小腸ではありません。"
        ]
    },
    {
        "qid": "ed29519badbec410",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。脳梗塞は主として貧血性梗塞（融解壊死）を生じます（再開通時に一部出血性梗塞となる場合もあります）。",
            "正しい。肺は肺動脈と気管支動脈による二重血行支配を受ける粗な組織構造であるため、血管閉塞時に出血性梗塞（赤色梗塞）を最も生じやすい臓器です。",
            "間違い。心筋梗塞は典型的な貧血性梗塞（凝固壊死）を生じます。",
            "間違い。腎梗塞は典型的な終動脈支配による貧血性梗塞を生じます。"
        ]
    },
    {
        "qid": "24fad8be6f9d3b73",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。広範囲熱傷では血漿成分の大量漏出により循環血液量減少性ショックを引き起こします。",
            "正しい。大量出血は循環血液量の急激な喪失により出血性（循環血液量減少性）ショックを引き起こします。",
            "正しい。重症細菌感染による敗血症ではエンドトキシン等により血管拡張・血圧低下を来す敗血症性ショック（血液分布異常性ショック）を引き起こします。",
            "間違い。浮腫は組織間隙に過剰な水分が貯留した病態であり、急激な組織灌流不全を本態とするショックの直接の原因や病態とは関連が低いです。"
        ]
    },
    {
        "qid": "f80bdfb5a2a68a47",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。結核結節は類上皮細胞やラングハンス巨細胞からなる特異性炎症（慢性肉芽腫性炎症）の病変です。",
            "間違い。肉芽組織は炎症後の修復・創傷治癒過程に形成される組織です。",
            "正しい。膿瘍は好中球浸潤と組織融解壊死を伴う限局性の化膿性炎症であり、急性炎症の代表的な病態です。",
            "間違い。瘢痕組織は肉芽組織が膠原線維に置換されて治癒した慢性期・治癒後の組織です。"
        ]
    },
    {
        "qid": "ebeb938f4ae71fb9",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。全身性エリテマトーデス（SLE）は自己の細胞核成分に対する自己抗体が産生され、全身諸臓器に免疫複合体沈着・炎症を生じる自己免疫疾患です。",
            "間違い。後天性免疫不全症候群（AIDS）はヒト免疫不全ウイルス（HIV）感染による感染性免疫不全症です。",
            "間違い。播種性血管内凝固症候群（DIC）は全身の微小血管内に多発性の血栓が形成される凝固異常症です。",
            "間違い。全身性炎症反応症候群（SIRS）は感染や外傷に対する生体の過剰な全身性炎症反応です。"
        ]
    },
    {
        "qid": "f8232823a6db3558",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。赤外線は温熱作用を持つ電磁波であり、細胞のDNAを直接損傷する電離作用や発がん作用は持たないため、発がん因子として適切ではありません。",
            "正しい。ダイオキシンは塩素系化合物であり、確立された化学発がん物質です。",
            "正しい。アスベスト（石綿）の吸入は中皮腫や肺癌を引き起こす物理化学的発がん因子です。",
            "正しい。EBウイルス（エプスタイン・バール・ウイルス）はバーキットリンパ腫や上咽頭癌などを誘発する腫瘍ウイルスです。"
        ]
    },
    {
        "qid": "5acd6afcae3f4763",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。子癇は妊娠後期や分娩時にみられる痙攣発作であり、後期妊娠中毒症（妊娠高血圧腎症）の重症型です。",
            "正しい。従来の分類において、妊娠初期（前期）に生じる悪心・嘔吐が重篤化した病態である「妊娠悪阻」は前期妊娠中毒症に含まれます。",
            "間違い。中毒症性脳出血は重症高血圧に伴う妊娠後期の重篤な合併症です。",
            "間違い。妊娠浮腫は妊娠後期に好発する中毒症症状です。"
        ]
    },
    {
        "qid": "8ccbac1ba35d9e43",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。クレチン病（先天性甲状腺機能低下症）では低身長、知能障害、粘液水腫などがみられます。",
            "正しい。クッシング病（クッシング症候群）では糖質コルチコイドの過剰分泌により、水牛様肩甲部脂肪沈着（バッファローハンプ）、満月様顔貌、中心性肥満を呈します。",
            "間違い。ターナー症候群（染色体異常：45,X）では低身長、翼状頸、性腺機能不全などがみられます。",
            "間違い。くる病はビタミンD欠乏による骨石灰化障害（O脚・X脚等）です。"
        ]
    },
    {
        "qid": "7f0c50ea11137e3f",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。先天性股関節脱臼（発育性股関節形成不全）は女児に圧倒的多数（男:女＝約1:5〜1:9）でみられます。",
            "正しい。ペルテス病（大腿骨頭無腐性壊死）は男児に好発（男:女＝約5:1）します。",
            "正しい。特発性側弯症は思春期の女子に著しく多くみられます（男:女＝約1:5〜1:8）。",
            "間違い。先天性筋斜頸（胸鎖乳突筋の線維化拘縮）は男女比がほぼ1:1であり、著明な性差はみられません。"
        ]
    },
    {
        "qid": "1fd7379e57dd86b9",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。20%は両脚支持期の割合などに相当します。",
            "間違い。40%は足が地面から離れている「遊脚相」の占める割合です。",
            "正しい。正常な平地歩行の1歩行周期において、足が地面に接地している「立脚相」の占める割合は約60%です。",
            "間違い。80%ではありません。"
        ]
    },
    {
        "qid": "d6d2fdfe03bd6de9",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。肘関節の良肢位は屈曲90度（前腕回内外中間位）です。",
            "正しい。手関節の良肢位は背屈（伸展）10〜20度です。",
            "間違い。膝関節の良肢位は「屈曲10〜20度（軽度屈曲位）」です。完全伸展0度のまま強直・拘縮すると歩行や着座動作が著しく障害されるため誤りです。",
            "正しい。足関節の良肢位は底背屈0度（中間位）から底屈5〜10度程度です。"
        ]
    },
    {
        "qid": "f777c34699d30b6f",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。閉塞性動脈硬化症（ASO）では末梢動脈の血流低下により下肢末梢に虚血性潰瘍や壊死を生じます。",
            "正しい。解離性大動脈瘤では胸部から背部・腰部にかけて引き裂かれるような激烈な疼痛を生じます。",
            "正しい。大動脈炎症候群（高安動脈炎）では大動脈弓分枝の狭窄により上肢橈骨動脈の拍動減弱・消失（脈なし病）を呈します。",
            "間違い。レイノー病は寒冷や情動刺激による末梢小動脈の血管攣縮・手指蒼白化を特徴とします。間欠跛行は閉塞性動脈硬化症（ASO）やバージャー病（TAO）に特徴的な症候です。"
        ]
    },
    {
        "qid": "8a361ffec5151ebd",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。B型肝炎ウイルスは血液・体液・母子垂直感染（非経口感染）します。",
            "間違い。C型肝炎ウイルスは血液を介して非経口感染します。",
            "間違い。D型肝炎ウイルスはB型肝炎ウイルス存在下で血液を介して感染します。",
            "正しい。E型肝炎ウイルス（およびA型肝炎ウイルス）は汚染された水や加熱不十分な豚・猪肉等の摂取により経口感染します。"
        ]
    },
    {
        "qid": "406dd02f40c66bbc",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。成人の胸骨圧迫速度は毎分100〜120回が適切です。",
            "正しい。成人の胸骨圧迫深度は約5cm（4〜5cm以上）沈む程度に行います。",
            "間違い。胸骨圧迫における加圧（圧迫）と除圧（圧迫解除）の時間比率は「1:1（50%:50%）」が適切です。1:2ではありません。",
            "正しい。圧迫位置は胸骨下部（剣状突起基部から約2横指頭側、胸骨の下半分）です。"
        ]
    },
    {
        "qid": "952bdc4c358cdfec",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。気管支拡張症では気管支壁の不可逆的拡張に伴い大量の膿性分泌物（痰）が貯留するため、重力を利用して排痰を促す体位ドレナージが極めて有効です。",
            "間違い。気管支喘息は気道の可逆性攣縮性狭窄が主体であり、主たる治療は気管支拡張薬等の薬物療法です。",
            "間違い。肺気腫は肺胞破壊と肺過膨張が主体であり、排痰よりも口すぼめ呼吸等の呼吸理学療法が用いられます。",
            "間違い。肺水腫は肺胞内への漏出液貯留であり、利尿薬や酸素投与、座位保持（起坐呼吸）等で心臓への静脈還流を減らす治療を行います。"
        ]
    },
    {
        "qid": "53c8aceecfeb6dda",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。大脳基底核の障害ではパーキンソニズムや不随意運動などの運動障害が生じます。",
            "間違い。小脳の障害では運動失調や協調不全が生じます。",
            "正しい。感覚解離（温痛覚脱失と触覚・深部感覚温存、あるいはその解離）は、伝導路の異なる線維が別々に走行する「脊髄」（脊髄空洞症や脊髄半切症候群等）の障害によって生じます。",
            "間違い。末梢神経の障害では支配領域の全感覚（触覚・温痛覚・深部感覚）が一様に障害されます。"
        ]
    },
    {
        "qid": "1d5a6f211d23288f",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。舞踏病様運動（コレア）は不規則で急激な比較的持続時間の短い運動ですが、ミオクローヌスよりは長いです。",
            "正しい。ミオクローヌスは筋肉の瞬間的な攣縮（数十〜数百ミリ秒単位の電撃的収縮）であり、不随意運動の中で持続時間が最も短いです。",
            "間違い。アテトーゼは四肢末梢のゆっくりとした這うような持続的な不随意運動です。",
            "間違い。ジストニアは持続的な筋収縮による異常姿勢や捻転運動です。"
        ]
    },
    {
        "qid": "fb35883c248bc64a",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。ロンベルグ試験は開眼時立位可能で閉眼時に動揺・転倒する（陽性）現象をみる検査であり、深部感覚障害による脊髄後索性運動失調の鑑別に最も適切です。",
            "間違い。反復拮抗運動（変換運動障害・回内回外試験）は小脳性運動失調の検査です。",
            "間違い。膝踵試験は小脳性運動失調による測定障害・協調不全を評価する検査です。",
            "間違い。書字試験は大字症（小脳障害）や小字症（パーキンソン病）などを評価する検査です。"
        ]
    },
    {
        "qid": "2744d266dfe1b97b",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。テタニーは副甲状腺機能低下症や過換気症候群などの低カルシウム血症によって生じる筋痙攣症状であり、甲状腺機能低下症ではみられません。",
            "正しい。声帯粘膜の浮腫（粘液水腫）により嗄声（かすれ声）を生じます。",
            "正しい。腸管蠕動運動の低下により頑固な便秘を生じます。",
            "正しい。精神機能および神経伝達の低下により言語緩慢や思考力低下を生じます。"
        ]
    },
    {
        "qid": "70f18a34c50664ea",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。尿崩症はバソプレッシン作用不足による多尿・口渇が主体であり、高血圧や低カリウム血症性四肢麻痺は特徴としません。",
            "正しい。原発性アルドステロン症ではアルドステロン過剰により、Na+再吸収増加（高血圧・高Na血症）とK+排泄増加（低K血症による筋力低下・四肢麻痺・多尿・多飲）を呈します。",
            "間違い。褐色細胞腫はカテコールアミン過剰による発作性高血圧、頭痛、発汗、動悸などが特徴です。",
            "間違い。副甲状腺機能亢進症は高カルシウム血症、骨病変、尿路結石などを特徴とします。"
        ]
    },
    {
        "qid": "937ebdb685d39049",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。腋毛脱落は副腎アンドロゲン分泌低下による症状です。",
            "正しい。アジソン病ではコルチゾール低下による負のフィードバック解除により下垂体前葉からのACTH（およびMSH活性をもつ前駆物質）分泌が増加し、皮膚・粘膜の色素沈着を引き起こします。",
            "間違い。低血圧はアルドステロンおよびコルチゾールの欠乏による循環血漿量減少に起因します。",
            "間違い。低血糖は糖新生を促進するコルチゾールの欠乏に起因します。"
        ]
    },
    {
        "qid": "480f973b64966d20",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。動眼神経核（外眼筋）はALSの4大陰性徴候（眼球運動障害がない）の一つであり、末期まで侵されません。",
            "間違い。三叉神経運動核（咀嚼筋）も侵されることがありますが、舌下神経核ほど早期・高頻度ではありません。",
            "間違い。顔面神経核（表情筋）も侵されますが、舌下神経核が最も早期かつ重篤に障害されます。",
            "正しい。筋萎縮性側索硬化症（ALS）の球麻痺では延髄の運動核が侵され、中でも舌の運動をつかさどる舌下神経核（第XII脳神経）が最も高頻度に障害され、舌筋萎縮や構音・嚥下障害を生じます。"
        ]
    },
    {
        "qid": "4ba212d7b450997b",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。ペルテス病は4〜8歳の男児に好発する大腿骨頭無腐性壊死です。",
            "間違い。変形性股関節症（脱臼続発症）は成人女性に多くみられます。",
            "間違い。結核性股関節炎は微熱や関節破壊を伴う感染症です。",
            "正しい。思春期（10〜15歳）の肥満男児に好発し、軽微な外傷や誘因のない股関節痛・跛行で発症するのは「大腿骨頭すべり症（骨端離開）」の典型像です。"
        ]
    },
    {
        "qid": "4a16fb39e3a71d86",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。スワンネック変形（PIP関節過伸展・DIP関節屈曲）は関節リウマチの代表的な手指変形です。",
            "正しい。ボタン穴変形（ボタン指変形：PIP関節屈曲・DIP関節過伸展）は関節リウマチの代表的な手指変形です。",
            "間違い。マレット変形（槌指）はDIP関節の伸筋腱断裂や剥離骨折による外傷性変形であり、関節リウマチの典型的関節病変ではありません。",
            "正しい。MP関節の尺側偏位は関節リウマチに特徴的な手指変形です。"
        ]
    },
    {
        "qid": "178e3c150a8f20a5",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。下部腰椎椎間板ヘルニア（L4/L5、L5/S1）はL5またはS1神経根を圧迫し坐骨神経痛を引き起こします。",
            "正しい。重い物の持ち上げ等の動作を契機とする急性腰痛（ぎっくり腰）で発症することが多いです。",
            "間違い。大腿内側の知覚障害はL2〜L3神経根（上位腰椎病変）または閉鎖神経障害で認められる所見であり、好発部位である下部腰椎ヘルニア（L5・S1神経根障害：下腿外側〜足背・足底）では認めにくいです。",
            "正しい。腰椎の後縦靭帯中央部が強固で外側が薄いため、髄核は解剖学的に後側方へ脱出することが大半です。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2005/00_source/question_2005_3.json"

for q_data, s_q in zip(questions_data, source_questions):
    assert q_data["qid"] == s_q["original_question_id"] == s_q["public_question_id"]
    
    # 10
    p10_list.append({
        "original_question_id": s_q["original_question_id"],
        "public_question_id": s_q["public_question_id"],
        "question_url": s_q["question_url"],
        "questionLabel": s_q["questionLabel"],
        "examLabel": s_q["examLabel"],
        "source_filepath": source_rel_path,
        "questionType": "true_false",
        "isCalculationQuestion": False
    })
    
    # 15
    p15_list.append({
        "original_question_id": s_q["original_question_id"],
        "public_question_id": s_q["public_question_id"],
        "question_url": s_q["question_url"],
        "questionLabel": s_q["questionLabel"],
        "examLabel": s_q["examLabel"],
        "source_filepath": source_rel_path,
        "questionIntent": q_data["intent"]
    })
    
    # 23
    p23_list.append({
        "original_question_id": s_q["original_question_id"],
        "public_question_id": s_q["public_question_id"],
        "question_url": s_q["question_url"],
        "questionLabel": s_q["questionLabel"],
        "examLabel": s_q["examLabel"],
        "source_filepath": source_rel_path,
        "correctChoiceText": q_data["choices_truth"]
    })
    
    # 21
    p21_list.append({
        "original_question_id": s_q["original_question_id"],
        "public_question_id": s_q["public_question_id"],
        "question_url": s_q["question_url"],
        "sourceQuestionKey": s_q["public_question_id"],
        "reviewQuestionId": s_q["public_question_id"],
        "sourceRecordRef": None,
        "questionLabel": s_q["questionLabel"],
        "explanationText": q_data["explanations"],
        "suggestedQuestionDetailsByChoice": [],
        "questionLearningPatternId": "principles_exceptions",
        "isLawRelated": False,
        "lawGroundedExplanationNotNeeded": True,
        "lawReferences": [[], [], [], []]
    })

p10_file = P10_DIR / "question_2005_3_questionType_fixed.json"
p15_file = P15_DIR / "question_2005_3_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2005_3_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2005_3_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2005_3 successfully.")
