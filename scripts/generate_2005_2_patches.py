# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2005 question_2005_2.json (Q26 - Q50)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2005/00_source/question_2005_2.json"

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
        "qid": "ef60931f4595f6a4",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。左右の冠状動脈は大動脈基部の大動脈洞（バルサルバ洞）、すなわち上行大動脈の起始部から分枝します。",
            "間違い。大動脈弓からは腕頭動脈、左総頸動脈、左鎖骨下動脈が分枝します。",
            "間違い。胸大動脈からは気管支動脈、食道動脈、肋間動脈などが分枝します。",
            "間違い。肺動脈は右心室から肺へ静脈血を送る血管であり、冠状動脈を分枝しません。"
        ]
    },
    {
        "qid": "6e193756acf66966",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。上腸間膜動脈は腹大動脈の前壁から出る無対性の動脈です。",
            "間違い。下腸間膜動脈は腹大動脈の前壁から出る無対性の動脈です。",
            "間違い。腹腔動脈は腹大動脈の前壁から出る無対性の動脈です。",
            "正しい。腎動脈（左右）は腹大動脈の側壁から左右一対となって分枝する有対性の動脈です。"
        ]
    },
    {
        "qid": "34982e8fb3ae5e6a",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。下垂体は頭蓋底の蝶形骨トルコ鞍（下垂体窩）に収まっています。",
            "正しい。下垂体は前葉・中葉（腺性下垂体）と後葉（神経性下垂体）から構成されます。",
            "正しい。視床下部からの放出・抑制ホルモンは下垂体門脈系を介して前葉に運ばれます。",
            "間違い。下垂体後葉ホルモン（オキシトシン、バソプレッシン）は視床下部の神経分泌細胞で産生され、後葉はそれらを貯蔵・分泌する場です。後葉自体にホルモン産生細胞は存在しません。"
        ]
    },
    {
        "qid": "b9529089bc91591a",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。視覚伝導路の中継核は「外側膝状体（LGB）」です。内側膝状体（MGB）は「聴覚伝導路」の中継核です。",
            "正しい。平衡覚情報は前庭神経を経て延髄・橋の前庭神経核で中継されます。",
            "正しい。味覚情報は顔面神経・舌咽神経・迷走神経を経て延髄の孤束核で中継されます。",
            "正しい。体性感覚伝導路（後索路や脊髄視床路）は視床の後外側腹側核（VPL核等）で中継されます。"
        ]
    },
    {
        "qid": "a9778b18e41b956a",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。腸骨下腹神経は腰神経叢（Th12-L1）から出ます。",
            "間違い。閉鎖神経は腰神経叢（L2-L4）から出ます。",
            "正しい。陰部神経（S2-S4）は仙骨神経叢から出て会陰部や外生殖器に分布します。",
            "間違い。大腿神経は腰神経叢（L2-L4）から出ます。"
        ]
    },
    {
        "qid": "619aebb6cf227224",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。顔面神経（第VII脳神経）は内耳道を通って側頭骨内に入り、茎乳突孔から頭蓋外に出ます。頸静脈孔は通りません。",
            "正しい。舌咽神経（第IX脳神経）は頸静脈孔を通って頭蓋外に出ます。",
            "正しい。迷走神経（第X脳神経）は頸静脈孔を通って頭蓋外に出ます。",
            "正しい。副神経（第XI脳神経）は頸静脈孔を通って頭蓋外に出ます。"
        ]
    },
    {
        "qid": "cfd3296db696ed99",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。内側直筋は動眼神経（第III脳神経）の支配を受けます。",
            "間違い。下斜筋は動眼神経（第III脳神経）の支配を受けます。",
            "間違い。下直筋は動眼神経（第III脳神経）の支配を受けます。",
            "正しい。上斜筋は滑車神経（第IV脳神経）の支配を受けます。"
        ]
    },
    {
        "qid": "3da022ecedd67990",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。半規管の膨大部稜には回転加速度を受容する有毛細胞が存在します。",
            "正しい。前庭の平衡斑（卵形嚢斑・球形嚢斑）には直線加速度・重力を受容する有毛細胞が存在します。",
            "正しい。蝸牛管のラセン器（コルチ器）には音刺激を受容する有毛細胞が存在します。",
            "間違い。鼓室階は外リンパで満たされた管であり、内部に有毛細胞（感覚受容器）は存在しません。"
        ]
    },
    {
        "qid": "e8a7b2de028994e7",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。呼吸数の減少（換気低下）は体内に二酸化炭素が蓄積するため、呼吸性アシドーシスの原因となります。",
            "正しい。腎機能の低下は酸の排泄障害や重炭酸イオンの産生低下を招き、代謝性アシドーシスの原因となります。",
            "間違い。頻回の嘔吐では胃酸（塩酸・水素イオン）が多量に体外へ失われるため、「代謝性アルカローシス」を引き起こします（アシドーシスの原因とはなりません）。",
            "正しい。持続する下痢ではアルカリ性の腸液（重炭酸イオン）が多量に喪失されるため、代謝性アシドーシスの原因となります。"
        ]
    },
    {
        "qid": "289c733e64712258",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。心臓は固有の自動能を持つため、摘出後も適切な環境下で一定時間拍動を維持します。",
            "正しい。洞房結節のペースメーカー細胞は一定のリズムで緩徐脱分極を繰り返し自動的に興奮します。",
            "正しい。刺激伝導系の興奮は特殊心筋線維（プルキンエ線維など）によって心室全体へ速やかに伝達されます。",
            "間違い。自律神経は心拍数や収縮力を調節する役割を果たしますが、心筋の収縮・拍動そのものは刺激伝導系の自動能によって発生するため、自律神経の働きは収縮に不可欠ではありません。"
        ]
    },
    {
        "qid": "5f32688f80246d01",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。血中酸素分圧が低下するとヘモグロビンからの酸素解離が促進されるため、酸素化ヘモグロビンの割合は減少します（増加しません）。",
            "正しい。腎臓の低酸素状態に反応してエリスロポイエチンの分泌が促進されます。",
            "正しい。低酸素刺激により頸動脈小体（化学受容器）の求心性活動が亢進し、呼吸中枢を刺激して換気を促進します。",
            "正しい。エリスロポイエチンの分泌増加により骨髄での赤血球産生が亢進し、赤血球数が増加します。"
        ]
    },
    {
        "qid": "f62f999c49af69cd",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。低蛋白血症では血漿膠質浸透圧が低下するため、血管内から組織間隙へ水分が移動して浮腫が生じます。",
            "正しい。抗体（免疫グロブリン）などの血清タンパク質が減少することで易感染性を呈します。",
            "間違い。多くの血液凝固因子（プロトロンビンやフィブリノゲンなど）は血漿タンパク質であるため、低蛋白血症では凝固因子の減少により出血傾向を生じ、血液凝固は抑制されます。",
            "正しい。血中アミノ酸・タンパク質の枯渇により末梢細胞へのアミノ酸供給が減少します。"
        ]
    },
    {
        "qid": "86bcfd5ee87a357a",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。二酸化炭素分圧の低下やpHの上昇は酸素解離曲線を左方へ移動させ、ヘモグロビンと酸素の親和性を高めます（ボーア効果）。",
            "正しい。肺の弾性収縮力により胸腔内圧は常に大気圧に対して陰圧に保たれています。",
            "間違い。肺胞気酸素分圧（約100〜104mmHg）は動脈血酸素分圧（約95〜100mmHg）よりも高く、濃度勾配に従って肺胞から血液中へ酸素が拡散します。",
            "正しい。腹式呼吸の吸気時には横隔膜が収縮して下降し、胸腔内容積を拡大させます。"
        ]
    },
    {
        "qid": "532641c4c8721396",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。食塊による胃壁の進展刺激は壁内神経叢や迷走神経を介して胃液分泌を促進します。",
            "間違い。迷走神経の興奮（アセチルコリン分泌）は壁細胞や主細胞を刺激して胃液分泌を促進します。",
            "間違い。ガストリンは胃幽門前庭部のG細胞から分泌され、胃酸分泌を強力に促進します。",
            "正しい。セクレチンは十二指腸粘膜のS細胞から分泌され、胃酸分泌および胃運動を抑制します。"
        ]
    },
    {
        "qid": "161a29d2a6fc7ec1",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。水は腸管腔内と細胞間の浸透圧勾配に従って受動的に吸収（浸透）されます。",
            "間違い。カルシウムイオンは能動輸送（カルシウム結合タンパク質やポンプ）によって吸収されます。",
            "間違い。ブドウ糖はNa+共輸送体（SGLT-1）を介した二次性能動輸送によって吸収されます。",
            "間違い。ナトリウムイオンはNa+/K+ポンプや共輸送体などの能動輸送によって吸収されます。"
        ]
    },
    {
        "qid": "ef6d5389470d0879",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。高温環境下では体温調節中枢の指令により発汗が亢進し、気化熱により体温を放散します。",
            "間違い。高温下での発汗により体液が喪失して血漿浸透圧が上昇するため、抗利尿ホルモンであるバソプレッシンの分泌は「増加」して水の再吸収が促進されます（減少は起こりません）。",
            "正しい。熱放散を促すために皮膚血管が拡張して皮膚血流量が増加します。",
            "正しい。発汗に伴うナトリウムの喪失や循環血漿量の減少により、アルドステロンの分泌が増加してNa+の再吸収が促進されます。"
        ]
    },
    {
        "qid": "567a7b5078acd67e",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。腎臓は酸塩基平衡保持のために重炭酸イオンを排泄するのではなく、再吸収および新規産生を行います。",
            "間違い。ナトリウムイオンの再吸収は体液量や浸透圧の維持に関与しますが、酸塩基平衡調節の直接の主要作用ではありません。",
            "正しい。腎臓の酸塩基平衡保持における最も重要な作用は、体内で生成された不揮発性酸の「水素イオン（H+）の尿中排泄」です。",
            "間違い。カリウムの再吸収・排泄は電解質バランスの調節に関与します。"
        ]
    },
    {
        "qid": "cbeac5bc67f5c153",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。ACTHは副腎皮質網状帯を刺激し、副腎アンドロゲンの分泌を増加させます。",
            "間違い。ACTHおよびコルチゾールの血中濃度上昇は視床下部に負のフィードバック（ネガティブフィードバック）を及ぼすため、CRH（ACTH放出ホルモン）の分泌は抑制・減少します。",
            "正しい。ACTHは副腎皮質球状帯にも軽度作用し、電解質コルチコイド（アルドステロン）の分泌を一部促進します。",
            "正しい。ACTHは副腎皮質束状帯を刺激し、糖質コルチコイド（コルチゾール）の分泌を強力に増加させます。"
        ]
    },
    {
        "qid": "19c0aad836892d20",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。乳汁産生は下垂体前葉ホルモンであるプロラクチンが関与します。",
            "正しい。血糖値上昇には下垂体前葉ホルモンである成長ホルモンやACTH（コルチゾール分泌刺激）が関与します。",
            "正しい。成長促進には下垂体前葉ホルモンである成長ホルモン（GH）が関与します。",
            "間違い。分娩時の子宮収縮を促すのは「下垂体後葉ホルモン」であるオキシトシンであり、下垂体前葉ホルモンは関与しません。"
        ]
    },
    {
        "qid": "4c6e7f121c91364d",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。交感神経カテコールアミンのα1受容体刺激は、血管平滑筋を収縮させて血圧を上昇させます。",
            "間違い。気管支平滑筋の拡張は「β2受容体」の刺激によって引き起こされます。",
            "間違い。心筋収縮力の増大や心拍数増加は「β1受容体」の刺激によって引き起こされます。",
            "間違い。胃腸管平滑筋の運動は交感神経刺激（α/β受容体）によって抑制（弛緩）されます。"
        ]
    },
    {
        "qid": "6b845bc4aae08f09",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。発汗を制御する交感神経節後線維（C線維）は無髄で細いため、圧迫に対する抵抗性が高く最後に障害されます。",
            "間違い。触覚を伝えるAβ線維は比較的太い線維ですが、最太の運動線維（Aα線維）よりは圧迫に耐えます。",
            "間違い。痛覚を伝えるAδ線維・C線維は細いため、圧迫に対して比較的保たれます。",
            "正しい。機械的圧迫に対しては線維径が太い神経線維ほど脆弱であるため、最も太い有髄線維であるAα線維（運動神経線維）が最初に障害されます。"
        ]
    },
    {
        "qid": "b330da43bbc84b79",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。α運動ニューロンは骨格筋の錐外筋線維に分布し、筋収縮を起こします。",
            "正しい。γ運動ニューロンは骨格筋内の筋紡錘（錐内筋線維）に分布し、感度を調節します。",
            "正しい。Ia群求心性線維は筋紡錘の一次終末に分布し、筋の伸張速度と長さを検出します。",
            "間違い。Ib群求心性線維は筋と骨を連結する「腱（腱紡錘・ゴルジ腱器官）」に分布し筋張力を検出するため、骨格筋線維（筋腹）自体には分布しません。"
        ]
    },
    {
        "qid": "f3bc84c015353d7b",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。前頭葉の中心前回（一次運動野）の損傷では随意運動麻痺が生じます。",
            "間違い。小脳の損傷では筋力低下（運動麻痺）は起こらず、運動失調（協調運動障害や企図振戦）や筋緊張低下が生じます。",
            "正しい。内包後脚（皮質脊髄路の通過部位）の損傷では対側の完全片麻痺が生じます。",
            "正しい。脊髄側索（外側皮質脊髄路の走行部位）の損傷では同側下位の痙性運動麻痺が生じます。"
        ]
    },
    {
        "qid": "e9f08be5eec95cff",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。ウェーバーの法則による計算値と一致しません。",
            "正しい。ウェーバーの法則（弁別閾ΔIと基準刺激Iの比kは一定）より、100gに対する弁別閾が3g（k=0.03）のとき、200gに対する弁別閾は 200 × 0.03 = 6g となり、区別できる最小の重さは 206g となります。",
            "間違い。ウェーバー比に基づき最小の重さは206gです。",
            "間違い。ウェーバー比に基づき最小の重さは206gです。"
        ]
    },
    {
        "qid": "bd2681f916c434f2",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。ビタミンAの欠乏によりロドプシン再合成が障害され、夜盲症（鳥目）を生じます。",
            "正しい。ビタミンB1（チアミン）の欠乏により脚気やウェルニッケ脳症を生じます。",
            "間違い。ビタミンC（アスコルビン酸）の欠乏症は「壊血病」です。悪性貧血（巨赤芽球性貧血）は「ビタミンB12」または葉酸の欠乏によって生じます。",
            "正しい。ビタミンDの欠乏によりカルシウム吸収が障害され、成人では骨軟化症（小児ではくる病）を生じます。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2005/00_source/question_2005_2.json"

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

p10_file = P10_DIR / "question_2005_2_questionType_fixed.json"
p15_file = P15_DIR / "question_2005_2_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2005_2_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2005_2_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2005_2 successfully.")
