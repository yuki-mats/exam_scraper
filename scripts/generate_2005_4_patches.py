# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2005 question_2005_4.json (Q76 - Q100)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2005/00_source/question_2005_4.json"

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
        "qid": "746c3e7bcf743d3f",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。僧帽弁狭窄症（MS）では左房圧上昇に伴う肺うっ血により起坐呼吸や夜間発作性呼吸困難を生じます。",
            "正しい。僧帽弁閉鎖不全症（MR）では左室からの逆流により有効心拍出量が低下し、易疲労性を生じます。",
            "正しい。大動脈弁狭窄症（AS）では左室流出路狭窄による心拍出量低下から脳虚血を来し、労作時失神発作を生じます。",
            "間違い。大動脈弁閉鎖不全症（AR）では拡張期に大動脈から左室へ血液が逆流するため、「拡張期血圧は低下」し脈圧が増大（水槌脈）します。"
        ]
    },
    {
        "qid": "7f351e34042cb508",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。白血病では正常白血球（好中球）の減少に伴う易感染性により発熱を好発します。",
            "正しい。悪性リンパ腫では骨髄浸潤や慢性炎症性変化に伴い貧血を生じます。",
            "間違い。特発性血小板減少性紫斑病（ITP）は血小板に対する自己抗体による破壊が主態であり、通常リンパ節腫大や肝脾腫を伴いません。",
            "正しい。血友病では凝固因子欠乏により関節内出血（関節血腫）を特徴的に繰り返します。"
        ]
    },
    {
        "qid": "b9db05271fcbda71",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。通常型膵癌の90%以上は「膵管上皮細胞」から発生する浸潤性膵管癌です。膵腺房細胞由来の癌は極めて稀です。",
            "正しい。膵癌は60〜70歳代の高齢男性に好発します。",
            "正しい。CA19-9は膵癌の代表的な血清腫瘍マーカーとしてスクリーニングや経過観察に用いられます。",
            "正しい。膵頭部癌では総胆管末端の狭窄・閉塞をきたしやすく、無痛性黄疸（閉塞性黄疸）を初発症状とします。"
        ]
    },
    {
        "qid": "84a1e334dd6b46e0",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。脳腫瘍摘出術は全身麻酔下で行われます。",
            "間違い。甲状腺手術は頸部手術であり全身麻酔下で行われます。",
            "間違い。上肢の手術は全身麻酔または腕神経叢ブロック等の伝達麻酔で行われます。",
            "正しい。脊椎麻酔（腰椎くも膜下麻酔）は下腹部や下肢・会陰部手術に適応があり、虫垂切除術の手術麻酔として可能です。"
        ]
    },
    {
        "qid": "6d8592714f68101d",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。末梢性顔面神経麻痺（ベル麻痺等）では顔面の血流改善を目的に星状神経節ブロック（SGB）が適応となります。",
            "正しい。頭頸部の帯状疱疹および帯状疱疹後神経痛に対する除痛・血流改善にSGBが適応となります。",
            "正しい。複合性局所疼痛症候群（CRPS/反射性交感神経性萎縮症）の上肢病変に対し交感神経ブロックとしてSGBが適応となります。",
            "間違い。片側顔面痙攣は顔面神経根部が頭蓋内血管に圧迫されて生じる運動神経疾患であり、微小血管減圧術やボツリヌス療法が適応となり、SGBの適応ではありません。"
        ]
    },
    {
        "qid": "af4beb59a1f1866b",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。低アルブミン血症による膠質浸透圧低下に伴い全身浮腫を生じます。",
            "間違い。ミオグロビン尿症は横紋筋融解症で筋肉が破壊された際に生じる病態であり、ネフローゼ症候群の特徴ではありません。",
            "正しい。糸球体基底膜の透過性亢進による大量蛋白尿に伴い低蛋白血症（低アルブミン血症）を生じます。",
            "正しい。肝臓でのリポ蛋白合成代償性亢進や異化低下により高脂血症（高コレステロール血症）を必発します。"
        ]
    },
    {
        "qid": "673d10a4fe87557f",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。慢性気管支炎は「喀痰を伴う咳嗽が少なくとも連続する『2年以上』にわたり、毎年『3か月以上』持続するもの」と定義されます。1か月以上ではありません。",
            "正しい。慢性気管支炎は慢性閉塞性肺疾患（COPD）を構成する閉塞性換気障害疾患です。",
            "正しい。気管支粘膜の杯細胞過形成や粘液腺肥大により、多量の喀痰を伴う湿性咳嗽を主症状とします。",
            "正しい。気道炎症や病態進行の最大の危険因子は喫煙であり、治療において禁煙が最も重要です。"
        ]
    },
    {
        "qid": "70300691e404d30c",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。肺線維症（特発性間質性肺炎）では胸膜病変を合併しない限り「胸痛」は通常みられません。",
            "正しい。肺間質・胞隔の線維化刺激により乾性咳嗽（空咳）を生じます。",
            "正しい。肺コンプライアンス低下と拡散障害により労作時呼吸困難（息切れ）を生じます。",
            "正しい。肺の進展性低下（拘束性換気障害）により肺活量減少（%VC低下）を生じます。"
        ]
    },
    {
        "qid": "979df8e76bd58659",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。破傷風菌（*Clostridium tetani*）は酸素の存在下では増殖できない偏性嫌気性菌です。",
            "間違い。破傷風は菌が産生する強力な神経外毒素（テタノスパスミン）が中枢神経抑制性介在ニューロンを遮断することで発症します。",
            "間違い。土壌などに存在する破傷風菌芽胞が刺傷や外傷部位から侵入する経皮（創傷）感染です。",
            "正しい。破傷風トキソイドワクチンの定期予防接種により高い発症予防効果が得られます。"
        ]
    },
    {
        "qid": "178ef2e36a879d27",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。オリーブ橋小脳萎縮症（多系統萎縮症MSA-C）では線条体病変に伴い筋強剛や寡動などのパーキンソニズムを生じます。",
            "正しい。小脳半球および虫部の萎縮に伴い体幹失調や四肢の運動失調を生じます。",
            "間違い。大脳皮質の言語中枢は侵されないため「失語症」はみられません（小脳失調による構音障害・失調性発語はみられます）。",
            "正しい。自律神経核の変性に伴い起立性低血圧や排尿障害などの自律神経症状を早期から伴います。"
        ]
    },
    {
        "qid": "9fc43f42bc9b8511",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。緊張型頭痛は後頭部から側頭部にかけての締め付けられるような頭痛であり、特異な前駆症状は伴いません。",
            "正しい。片頭痛（前兆のある片頭痛）では頭痛発作の直前に閃輝暗点などの視覚性前兆や感覚性前駆症状を伴います。",
            "間違い。三叉神経痛は顔面の突発的な電撃様激痛であり、前駆症状なく突然発症します。",
            "間違い。大後頭神経痛は後頭部の発作性神経痛であり、前兆は伴いません。"
        ]
    },
    {
        "qid": "2db0c18bb861c1d2",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。させられ体験（作為体験）は統合失調症に特徴的な自我障害（精神病症状）であり、心身症の特徴ではありません。",
            "正しい。身体症状に伴う強い不安感や緊張感を伴うことが多いです。",
            "正しい。多彩な自律神経症状や身体的愁訴を訴えます。",
            "正しい。心身症は器質的または機能的身体疾患であり、その発症や経過に心理社会的ストレスが密接に関与します。"
        ]
    },
    {
        "qid": "558e2c876458f77b",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。アレルギー性鼻炎は花粉やダニ抗原に対するIgE抗体が関与する即時型（I型）アレルギーです。",
            "正しい。アレルギー性鼻炎の診断において、鼻汁塗抹検査による好酸球の確認は極めて重要な検査です。",
            "間違い。通年性アレルギー性鼻炎など慢性に経過することが多いです。",
            "間違い。鼻粘膜の好酸球性炎症や自然口の狭窄により副鼻腔炎を合併しやすいです。"
        ]
    },
    {
        "qid": "7517303393e018da",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。人工内耳の適応判定・手術は耳鼻咽喉科医師が行い、機器調整（マッピング）等を言語聴覚士が担当します。作製業務ではありません。",
            "正しい。作業療法士（OT）は日常生活動作（ADL）や社会適応を支援するため、患者に合わせた自助具の作製・選定・指導を行います。",
            "間違い。神経ブロック注射は医師が行う医行為であり、理学療法士の業務ではありません。",
            "間違い。介護支援専門員（ケアマネジャー）はケアプラン作成が主業務であり、体操指導は理学療法士等の業務です。"
        ]
    },
    {
        "qid": "f3f8af44697bd282",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。MMT4（Good）は中等度の抵抗に抗して全可動域を動かせる筋力です。",
            "間違い。MMT3（Fair）は重力に抗して全可動域を動かせる筋力です。",
            "正しい。MMT2（Poor）は重力を除外した肢位（水平面上の運動）であれば全可動域を動かせる筋力です。",
            "間違い。MMT1（Trace）は関節運動は起こらないが筋収縮を触知できる状態です。"
        ]
    },
    {
        "qid": "5a16391e504a2888",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。意識障害は中枢神経障害などの原疾患による症候であり、廃用症候群の直接症候ではありません。",
            "正しい。長期の安静・不動状態により関節構成体や周囲軟部組織が線維化し、関節拘縮を生じるのは廃用症候群の代表例です。",
            "間違い。尿失禁は骨盤底筋低下等で生じることもありますが、関節拘縮ほど直接的・代表的な症候ではありません。",
            "間違い。けいれんは脳の異常放電等による症候です。"
        ]
    },
    {
        "qid": "2be5227c93d2adfb",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。頸髄不全麻痺では錐体路障害により下肢に痙性麻痺（筋緊張亢進・痙縮）を生じます。",
            "正しい。パーキンソン病では錐体外路障害により全身および下肢に鉛管様・歯車様の筋強剛（筋緊張亢進）を生じます。",
            "正しい。痙直型脳性麻痺では上位運動ニューロン障害により下肢の筋緊張が著明に亢進します（ハサミ状歩行等）。",
            "間違い。腰椎椎間板ヘルニアは下位運動ニューロン（脊髄神経根）障害であり、下肢の弛緩性麻痺（筋緊張低下・腱反射減弱）を呈するため筋緊張は増強しません。"
        ]
    },
    {
        "qid": "2311ed627ac070eb",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。外反母趾には足底挿板（インソール）や夜間用外反母趾装具を用います。",
            "間違い。コックアップスプリント（手関節背屈保持装具）は「橈骨神経麻痺（下垂手）」に用います。",
            "間違い。PTB装具（膝蓋腱部免荷装具）は下腿骨骨折の免荷歩行に用います。片麻痺には短下肢装具（AFO）が用いられます。",
            "正しい。腰椎圧迫骨折では脊柱の安静固定と過伸展保持・免荷を目的に硬性・軟性の体幹装具（ダーメンコルセット等）を用います。"
        ]
    },
    {
        "qid": "4b8da4ba1cc510c5",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。第3頸髄（C3）レベルの完全脊髄損傷では横隔神経（C3-C5）が麻痺して自発呼吸が不能となるため、人工呼吸器管理が必須となります。",
            "間違い。C7レベル損傷では上腕三頭筋が機能するため、手動車いすの自力駆動や自立移乗が可能です。",
            "間違い。Th3レベル完全麻痺では体幹機能の低下により車いす自立生活が主体となります。",
            "間違い。Th12レベル完全麻痺では股関節周囲筋の麻痺があるため長下肢装具（KAFO）等が必要となります。短下肢装具で歩行可能なのはL4以下です。"
        ]
    },
    {
        "qid": "51efa41c282ee95a",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。呼吸困難感や過緊張を和らげるためリラクゼーション手技を用います。",
            "正しい。口すぼめ呼吸は気道内圧を維持して末梢気道の早期閉塞を防ぎ、換気効率を改善します。",
            "間違い。速い浅い呼吸パターンは死腔換気を増やし換気不全を悪化させるため、ゆっくりと深く呼気時間を長く取る「腹式呼吸・緩徐呼吸」を指導します。",
            "正しい。呼吸筋や歩行能力を維持するため四肢・体幹の筋力強化訓練を行います。"
        ]
    },
    {
        "qid": "c04ae4888d018d0b",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。骨折手術直後の急性期創部へのホットパック（温熱療法）は、局所の充血・血管拡張により出血や炎症反応・浮腫を増悪させるため禁忌であり行いません。",
            "正しい。深部静脈血栓予防や関節拘縮予防のため、手術直後から愛護的な関節可動域訓練を開始します。",
            "正しい。褥瘡予防や肺合併症予防のため、ベッド上での定期的な体位変換を行います。",
            "正しい。廃用症候群防止のため、全身状態が安定次第早期から車いす座位訓練を行います。"
        ]
    },
    {
        "qid": "c64a73e50602cbe3",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。衛気は水穀の精気のうち「剽悍滑利」な陽気であり、脈外を巡って体表を温煦・防衛します。「水穀の精気」そのものの精華で脈中を行くのは「営気」です。",
            "正しい。真気（正気）は原気・宗気・営気・衛気を統合した気であり、全身を温煦・滋養します。",
            "正しい。宗気は自然界の清気と水穀の精気が合わさって胸中（膻中）に集まります。",
            "正しい。営気は水穀の精気から生じ、血とともに脈中を巡って臓腑を滋養します。"
        ]
    },
    {
        "qid": "d16336c40dd1d1b8",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。陰陽可分は陰陽の事物はさらに無限に陰陽へ細分化できる法則です。",
            "正しい。「陰極まれば陽となり、陽極まれば陰となる」「重陰は必ず陽となり、重陽は必ず陰となる」は、事物が極限に達した際に正反対へ変化する「陰陽転化」の法則です。",
            "間違い。陰陽消長は陰陽の一方が増えれば他方が減るという量的変化の法則です。",
            "間違い。陰陽互根は陰陽が互いを存在の根拠とし依存し合う法則です。"
        ]
    },
    {
        "qid": "12118abf146b302c",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。五行の相生関係において「土生金、金生水」であり、肺金は腎水の母経です。",
            "正しい。膀胱水が胆木（水生木）を生じるため、膀胱経の子経は胆経です。",
            "間違い。五行関係において脾は「土」、肺は「金」であり、「土生金」の相生関係（脾が母、肺が子）です。相剋関係（火克金、金克木）ではないため誤りです。",
            "正しい。肝木が心火を生じる（木生火）ため、心経の相生（母）の経は肝経です。"
        ]
    },
    {
        "qid": "0ba94b84276a752e",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。東洋医学の古典において五臓の付着部として、肺は「第3胸椎（身柱・肺兪の高さ）」に付くとされます。",
            "間違い。口唇に開竅するのは「脾」です（肺は鼻に開竅します）。",
            "間違い。作強の官は「腎」です（肺は相傅の官です）。",
            "間違い。統血をつかさどるのは「脾」です（脾不統血）。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2005/00_source/question_2005_4.json"

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

p10_file = P10_DIR / "question_2005_4_questionType_fixed.json"
p15_file = P15_DIR / "question_2005_4_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2005_4_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2005_4_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2005_4 successfully.")
