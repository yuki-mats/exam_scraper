# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2006 question_2006_6.json (Q126 - Q150)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2006/00_source/question_2006_6.json"

P10_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/10_questionType_fixed"
P15_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/15_correctChoiceText_fixed"
P23_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/23_correctChoiceText_fixed"
P21_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/21_explanationText_added"

for p in [P10_DIR, P15_DIR, P23_DIR, P21_DIR]:
    p.mkdir(parents=True, exist_ok=True)

with open(SOURCE_FILE, "r", encoding="utf-8") as f:
    source_data = json.load(f)
    source_questions = source_data.get("question_bodies", source_data)

questions_data = [
    {
        "qid": "92bb42a795b78351",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。ドケルバン病（狭窄性腱鞘炎）は、手関節橈側（第1腱区画）を通る長母指外転筋腱および短母指伸筋腱の腱鞘炎であり、長母指外転筋が罹患筋となります。",
            "間違い。母指内転筋は尺骨神経支配の内在筋であり、第1腱区画を通りません。",
            "間違い。短母指屈筋は正中神経および尺骨神経支配の内在筋であり、手関節背側の腱区画を通りません。",
            "間違い。母指対立筋は正中神経支配の内在筋であり、第1腱区画を通りません。"
        ]
    },
    {
        "qid": "2e00fafe1a40460b",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。L3神経根障害では大腿前部の感覚障害や大腿四頭筋の筋力低下がみられます。",
            "間違い。L4神経根障害では下腿内側の感覚障害、前脛骨筋の筋力低下、膝蓋腱反射の減弱・消失がみられます。",
            "正しい。長母指伸筋および長指伸筋の筋力低下、下腿外側から足背にかけての感覚鈍麻、膝蓋腱・アキレス腱反射の正常は、L5神経根障害の典型的な所見です。",
            "間違い。S1神経根障害では足底・足外側の感覚障害、下腿三頭筋・長短腓骨筋の筋力低下、アキレス腱反射の減弱・消失がみられます。"
        ]
    },
    {
        "qid": "d69f6891ee2b1329",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。承山は下腿三頭筋（足関節底屈筋・主動筋側）上に位置します。",
            "間違い。飛陽は下腿三頭筋・腓骨筋群（主動筋側）上に位置します。",
            "間違い。陰陵泉は脛骨内側顆下縁に位置し、底屈の拮抗筋（前脛骨筋など）上ではありません。",
            "正しい。足関節底屈麻痺に対する拮抗筋（足関節背屈筋＝前脛骨筋）の緊張緩和を目的とする場合、前脛骨筋上に位置する足三里への施術が適切です。"
        ]
    },
    {
        "qid": "e1c72ae7a011d62d",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。膝の外反ストレステスト陽性は内側側副靭帯損傷を示し、膝関節内側に位置する曲泉への局所治療が適切です。",
            "正しい。チェアテスト陽性は上腕骨外側上顆炎（テニス肘）を示し、腕橈骨筋・橈側手根伸筋部に位置する曲池への局所治療が適切です。",
            "間違い。ファレンテスト陽性は正中神経麻痺（手根管症候群）を示し、手関節掌側の大陵や内関が治療穴となります。手関節背側にある陽池は適切ではありません。",
            "正しい。パトリックテスト陽性は股関節疾患や仙腸関節障害を示し、股関節・殿部に位置する環跳への局所治療が適切です。"
        ]
    },
    {
        "qid": "21ae98f08a8255cc",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。湿邪による咳・痰や倦怠感・関節痛に対し、水湿の運化をつかさどる脾の機能を高めることは適切です。",
            "間違い。肝の疏泄を促す（疎肝理気）手技は肝気鬱結証に対する治法であり、湿痰阻肺・脾虚湿盛の病態に対する治療目的としては適切ではありません。",
            "正しい。高湿度による咳・痰や関節痛（痰湿）に対し、痰湿を除去することは適切です。",
            "正しい。痰による咳に対し、肺の粛降作用を整えて鎮咳を図ることは適切です。"
        ]
    },
    {
        "qid": "69389d0424faec17",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。天枢は足の陽明胃経、太白は足の太陰脾経に属するため、同経配穴ではなく表裏配穴（異経）の組合せであり誤りです。",
            "正しい。中脘（腹部任脈・胃の募穴）と胃兪（背部膀胱経・胃の背部兪穴）の配穴は、腹部と背部を組み合わせた前後配穴です。",
            "正しい。足三里（胃経）と公孫（脾経）の配穴は、表裏関係にある経脈の経穴を組み合わせた表裏配穴です。",
            "正しい。内関（上肢心包経）と足三里（下肢胃経）の配穴は、上肢と下肢の経穴を組み合わせた上下配穴です。"
        ]
    },
    {
        "qid": "65b160202e617d93",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。『難経六十八難』において、井穴は「心下満」を主ります。",
            "間違い。『難経六十八難』において、兪穴は「体重節痛」を主ります。",
            "間違い。『難経六十八難』において、経穴は「喘咳寒熱」を主ります。",
            "正しい。『難経六十八難』において、合穴は「逆気而泄（気の逆上によるのぼせや下痢）」を主治と定めています。"
        ]
    },
    {
        "qid": "fea87ac680ff6bf1",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。陽維脈は全身の陽経を統括し、悪寒や発熱などの表証・外感病に関与するため、本症例のような気血両虚・希発月経の治療には適切ではありません。",
            "正しい。衝脈は「血海」「十二経の海」と呼ばれ、月経や生殖機能に深く関与するため治療穴として適切です。",
            "正しい。任脈は「陰脈の海」と呼ばれ、胞中（子宮）から起こり月経・妊娠をつかさどるため治療穴として適切です。",
            "正しい。帯脈は諸経脈を束ねて下腹部・腰部を安定させ、婦人科疾患の治療に用いられます。"
        ]
    },
    {
        "qid": "e33a0df3c69a88f3",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。肩峰を結んだ線上は第7頸椎棘突起付近の高さです。",
            "正しい。小児の疳の虫に対する名灸穴である「身柱（ちりけの灸）」は第3胸椎棘突起下にあり、左右の肩甲棘内側端を結んだ線上（第3胸椎棘突起の高さ）に位置します。",
            "間違い。第12肋骨先端の高さは第2腰椎棘突起付近です。",
            "間違い。腸骨稜の最高点を結ぶ線（ヤコビー線）は第4腰椎棘突起（またはL4-L5棘間）の高さです。"
        ]
    },
    {
        "qid": "5ea9cd70df53b78b",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。太白は足の太陰脾経の兪土穴・原穴です。",
            "間違い。経渠は手の太陰肺経の経金穴です。",
            "正しい。『難経六十九難』の「虚すればその母を補う」原則に基づき、肝（木）の母である水（腎）を補うため、母経である足の少陰腎経の合水穴である陰谷（または自経の合水穴である曲泉）に補法を行います。",
            "間違い。少府は手の少陰心経の栄火穴です。"
        ]
    },
    {
        "qid": "9f8f08ab89f82aab",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。有痛性外脛骨は舟状骨内側（足部内側）の疼痛と圧痛を特徴とします。",
            "間違い。オスグッド病は脛骨粗面（膝蓋靭帯付着部）の疼痛と隆起を特徴とします。",
            "間違い。コンパートメント症候群は下腿区画内圧上昇による激痛、知覚異常、血行障害などを生じます。",
            "正しい。陸上選手における脛骨下1/3内後縁の疼痛、X線での骨折像なし、足関節底屈・内反抵抗運動での疼痛誘発は、シンスプリント（脛骨過労性骨膜炎）の典型例です。"
        ]
    },
    {
        "qid": "2ee0e137826bdf3d",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。前脛骨筋は足関節の背屈・内反をつかさどる筋肉です。",
            "正しい。シンスプリントで足関節の底屈・内反抵抗運動により疼痛が誘発される場合、脛骨後内側面に付着する後脛骨筋（および長趾屈筋）が主たる罹患筋となります。",
            "間違い。長腓骨筋は足関節の底屈・外反をつかさどる筋肉です。",
            "間違い。短腓骨筋は足関節の外反をつかさどる筋肉です。"
        ]
    },
    {
        "qid": "a6c990c4c973b88c",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。血圧測定時は上腕（マンシェット）を心臓の高さに合わせて測定します。",
            "間違い。通常の随時血圧（診察室血圧）測定は、背もたれ付きの椅子に座った「座位（安静座位）」で行うのが原則であり、仰臥位での測定は誤りです。",
            "正しい。減圧過程でコロトコフ音が聴取され始める点（スワンの第1点）を収縮期血圧とします。",
            "正しい。マンシェットの減圧速度は1秒間に2〜3mmHg程度で徐々に下げていくのが適切です。"
        ]
    },
    {
        "qid": "e79800ce593be2bc",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。高血圧の放置により心肥大、虚血性心疾患、心不全などの心臓障害を好発します。",
            "間違い。高血圧の放置により腎硬化症や腎不全などの腎臓障害を好発します。",
            "正しい。高血圧の直接的な標的臓器障害は心臓・脳・腎臓・血管・眼底であり、肝臓は高血圧による直接の臓器障害を受けにくい臓器です。",
            "間違い。高血圧の放置により脳出血や脳梗塞などの脳血管障害を好発します。"
        ]
    },
    {
        "qid": "53fd1e097cdc6ebb",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。気虚証では自汗、息切れ、疲労感、舌質淡、脈虚などがみられます。",
            "間違い。血虚証では顔色蒼白、めまい、目のかすみ、爪の変色、舌質淡などがみられます。",
            "正しい。夜間頻尿、手足のほてり（五心煩熱）、腰の重だるさ、舌質紅・無苔、脈浮で無力（虚火上炎・腎水不足）は「陰虚証（腎陰虚）」の特徴です。",
            "間違い。瘀血証では刺痛、固定性の疼痛、舌質紫暗、瘀点・瘀斑、脈渋などがみられます。"
        ]
    },
    {
        "qid": "950d1761ed0dffee",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。毫鍼の安全性確保のため、引張り強度試験を行って折鍼を防止することは品質保証基準に含まれます。",
            "正しい。単回使用毫鍼では滅菌有効期限および滅菌年月を表示することが義務付けられています。",
            "正しい。耐食性と生体適合性を保つため、ステンレス鋼線などの安全な医療用金属を使用します。",
            "間違い。伝導性検査は鍼灸用毫鍼の日本産業規格（JIS）等の品質保証基準には規定されておらず、適切ではありません。"
        ]
    },
    {
        "qid": "86cc6593824059ff",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。膝窩嚢胞（ベーカー嚢胞）は膝窩部の滑液包炎・関節液貯留病態であり、関節穿刺事故の直接結果ではありません。",
            "間違い。離断性骨軟骨炎は骨端症や繰り返す力学的負荷によって生じる骨軟骨障害です。",
            "正しい。膝関節腔内へ鍼が深く刺入された場合、無菌操作の不徹底や皮膚細菌の迷入によって化膿性膝関節炎（細菌性関節炎）を引き起こす危険性があります。",
            "間違い。膝蓋軟骨軟化症は膝蓋骨関節軟骨の変性疾患です。"
        ]
    },
    {
        "qid": "1d23b41d8d14c48c",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。臨床において鍼通電療法では、鎮痛を目的として1〜10Hz前後の低頻度パルスが多用されます。",
            "間違い。直流電流を持続的に通電すると電気分解（電蝕）を起こして鍼体が腐食し折鍼の原因となるため、通電には交流電流または双方向性パルス波を用います。",
            "正しい。低頻度鍼通電による鎮痛は内因性オピオイドの放出を伴うため、刺激終了後も持続性のある鎮痛効果が得られます。",
            "正しい。電気刺激では陰極直下で脱分極が生じやすいため、陰極側の興奮閾値は陽極側よりも低くなります。"
        ]
    },
    {
        "qid": "bd85c4b459afcd38",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。大鍼は先端が丸く関節液の排出や水腫の治療に用いられた太い鍼です。",
            "正しい。古代九鍼のうち「円鍼」は先端が卵のように丸く、筋肉の間を擦って皮膚を傷つけずに刺激する鍼であり、擦過刺激を中心とする小児鍼（接触鍼・皮膚鍼）の起源となりました。",
            "間違い。鍉鍼は先端が黍粒状で経脈を押圧する鍼であり、押圧刺激に用いられますが、小児鍼の擦過手技の起源としては円鍼が対応します。",
            "間違い。毫鍼は細く柔らかい鍼で、現代の刺鍼手技の主体です。"
        ]
    },
    {
        "qid": "e36b4842f6371ef9",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。管散術は鍼管を用いて皮膚を軽打・擦過する手技であり、触圧覚受容器を刺激して主として太い有髄求心性線維であるAβ線維を興奮させます（ゲートコントロール機構の賦活）。",
            "間違い。置鍼術は筋層などに刺入して留置する手技で、深部受容器（Aδ・C線維など）が関与します。",
            "間違い。間歇術は刺入と引き上げを繰り返す手技で、ポリモーダル受容器や深部感覚受容器を刺激します。",
            "間違い。屋漏術は深部に刺入して上下に小刻みに動かす瀉法手技で、侵害受容器（Aδ・C線維）が強く関与します。"
        ]
    },
    {
        "qid": "17c55a10b5e3e994",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。D-フェニルアラニンは内因性オピオイド分解酵素（エンケファリナーゼ等）を阻害するため、エンドルフィンやエンケファリンの分解を抑えて鍼鎮痛効果を増強・発現しやすくします。",
            "間違い。ナロキソンはオピオイド受容体拮抗薬であり、内因性オピオイドの作用を遮断して鍼鎮痛を消失させます。",
            "間違い。アルギニンは一酸化窒素合成などの前駆体となるアミノ酸であり、鍼鎮痛発現促進の直接的な分解阻害薬ではありません。",
            "間違い。ロイシンは分岐鎖アミノ酸（BCAA）の一つであり、鍼鎮痛発現の特異的促進物質ではありません。"
        ]
    },
    {
        "qid": "bbc15fe736a6adff",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。内調術は鍼の周囲の皮膚を軽打して筋緊張を緩める手技です。",
            "正しい。随鍼術は患者の呼吸運動（呼気・吸気）や体動に合わせて刺入・抜鍼・運鍼を行う手技です。",
            "間違い。間歇術は刺入した鍼を一定の深さで進退させる手技です。",
            "間違い。副刺激術は刺鍼中に刺鍼部位の周囲を叩打・圧迫する手技の総称です。"
        ]
    },
    {
        "qid": "b637983b91456523",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。刺鍼時に筋肉の痙攣などにより抜鍼困難となった場合、鍼の周囲を指先で叩く示指打法や副刺激を行って筋緊張を緩めます。",
            "正しい。血管損傷による内出血が生じた場合、直ちに刺鍼部を圧迫止血（および冷罨法）します。",
            "正しい。抜鍼後に違和感や鈍重感などの遺感覚が残る場合、刺鍼部周辺の後揉捏を行って循環を促します。",
            "間違い。気胸が疑われる場合、患者を安静臥位に保ちバイタルサインを確認の上、直ちに専門医や救急医療機関へ搬送する必要があります。返し鍼（迷走神経反射への手技）を行う処置は極めて不適切です。"
        ]
    },
    {
        "qid": "49773c6ca1c6b37a",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。ポリモーダル受容器の形態学的構造は、特殊な終末器を持たない自由神経終末です。",
            "正しい。ポリモーダル受容器は機械刺激、温熱刺激、化学刺激の多種類の刺激に応答します。",
            "正しい。ポリモーダル受容器からの求心性入力は、中枢を介して体性−自律神経反射（血流変化や内臓機能調節）を誘発します。",
            "間違い。骨格筋の張力を検出する受容器は「腱紡錘（ゴルジ腱器官、Ib線維）」であり、ポリモーダル受容器の機能ではありません。"
        ]
    },
    {
        "qid": "7b41ab5792cce846",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。中脳水道周囲灰白質（PAG）は下行性疼痛抑制系の起点（上位中枢）です。",
            "間違い。延髄大縫線核（NRM）は中脳からの入力を受けてセロトニン作動性線維を脊髄へ投射する中継部位です。",
            "正しい。下行性抑制系は「脊髄後角（膠様質）」においてエンケファリン作動性介在ニューロンを活性化し、末梢からの一次求心性線維（痛覚情報）の伝達をシナプス前・シナプス後抑制によって遮断します。",
            "間違い。後根神経節（DRG）は一次感覚ニューロンの細胞体が存在する部位であり、下行性抑制系が直接シナプス遮断を行う部位ではありません。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2006/00_source/question_2006_6.json"

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

p10_file = P10_DIR / "question_2006_6_questionType_fixed.json"
p15_file = P15_DIR / "question_2006_6_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2006_6_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2006_6_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2006_6 successfully.")
