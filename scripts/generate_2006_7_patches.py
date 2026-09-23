# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2006 question_2006_7.json (Q151 - Q160)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2006/00_source/question_2006_7.json"

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
        "qid": "d36b03791d914f07",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。透熱灸では燃焼温度が穏やかで灰立ちの良い良質モグサ（夾雑物が少ないモグサ）を用います。",
            "正しい。艾炷は手掌や指先で円錐形（米粒大・半米粒大など）に成形します。",
            "正しい。透熱灸は直接皮膚上に艾炷を据えて点火し燃焼させる有痕灸です。",
            "間違い。患者が熱さを感じたところで取り去る灸法は「知熱灸」や「八分灸（無痕灸）」です。透熱灸は艾炷が灰になるまで完全に燃焼させます。"
        ]
    },
    {
        "qid": "2cb1025fdd3ca2ad",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。繊維が細かく均一なモグサは直接灸（透熱灸）に適した良質モグサの特徴です。",
            "正しい。間接灸（温灸・隔物灸）では皮膚との間に介在物や空間を置くため、燃焼温度が高く火力の持続する粗悪モグサ（温灸用モグサ）を用います。",
            "間違い。淡黄白色であることは良質モグサの特徴であり、間接灸用モグサは暗緑褐色〜黄褐色です。",
            "間違い。燃焼時の煙が少ないことは良質モグサの特徴であり、間接灸用モグサは煙や特有の匂いが多く発生します。"
        ]
    },
    {
        "qid": "fc5e6661abd0c791",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。墨灸は墨汁を塗布した紙などの上から温める隔物灸・温灸の一種です。",
            "間違い。ショウガ灸は薄切りにした生姜を皮膚上に置いて艾炷を燃焼させる隔物灸です。",
            "正しい。焦灼灸はイボや魚の目、胼胝などの皮膚硬結部に直接多壮灸をすえる直接灸であり、強烈な温熱作用とともにモグサ燃焼時に生じるタール成分（チモール等の消毒・腐食作用）が直接組織に作用します。",
            "間違い。知熱灸は患者が温熱を感じた時点で艾炷を取り去る間接的・無痕的な灸法です。"
        ]
    },
    {
        "qid": "b9643e57bea3d27d",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。棒灸はモグサを棒状に巻いて点火し、皮膚から一定の距離を保って輻射熱で温める温灸（間接灸）の一種です。",
            "間違い。ウルシ灸は漆などの薬物を皮膚に塗布して発赤・水疱を生じさせる薬灸・天灸の一種であり、隔物灸ではありません。",
            "間違い。八分灸は艾炷が8割程度燃焼したところで取り去る知熱灸・無痕灸の一種であり、透熱灸（有痕灸）ではありません。",
            "間違い。糸状灸は糸状の極めて微細な艾炷を用いる透熱灸（直接灸）の一種であり、知熱灸ではありません。"
        ]
    },
    {
        "qid": "ebdec3fcc781533c",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。灸あたりによる発熱時に多壮灸を行うと、過剰刺激となり全身症状を悪化させます。",
            "間違い。灸痕が化膿した場合、刺激性の強い逆性石けん等での消毒は避け、生理食塩水や流水で愛護的に洗浄・保護します。",
            "正しい。灸あたり（過剰刺激）による全身倦怠感や脱力感に対しては、安静臥床させ、保温と水分補給を行って回復を促します。",
            "間違い。のぼせが生じた場合、頸部を温めると熱感が助長されるため、頭頸部を冷やすか足部を温めて気を下降させます。"
        ]
    },
    {
        "qid": "17b9e6a687a8a5a6",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。灸による温熱・侵害刺激は脊髄視床路を通って視床（VPL核等）に達します。",
            "間違い。温熱・痛覚情報は上行途中で脳幹網様体に側枝を出し、自律神経反射や覚醒反応を賦活します。",
            "正しい。後索核（薄束核・楔状束核）は後索を通る精細触圧覚や深部感覚（Aβ・Aα線維）の中継核であり、温熱・侵害熱刺激（前外側索・脊髄視床路系）の伝導路には関与しません。",
            "間違い。灸熱刺激は無髄のC線維（ポリモーダル受容器・温受容器）を介して脊髄後角へ入力されます。"
        ]
    },
    {
        "qid": "689635a2342052cf",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。温受容器の至適反応温度は約38〜43℃であり、50℃付近では温受容器ではなく痛覚受容器（熱痛）が強く反応します。",
            "正しい。温受容器の組織学的形態は、特殊な終末構造を持たない自由神経終末です。",
            "間違い。温覚情報は主として無髄のC線維（IV群線維）によって伝導されます。II群線維は触圧覚や筋紡錘二次終末です。",
            "間違い。温受容器は持続的な同一温度刺激に対して徐々に興奮頻度が低下する順応現象を示します。"
        ]
    },
    {
        "qid": "7f952f948e55d8f7",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。ブラジキニンは熱刺激・組織損傷に伴って生成される代表的な炎症性ケミカルメディエーターです。",
            "間違い。プロスタグランジンはアラキドン酸から合成され、血管拡張や発痛増強に関与する炎症メディエーターです。",
            "間違い。ヒスタミンは肥満細胞などから遊離され、血管透過性亢進や浮腫を引き起こす炎症物質です。",
            "正しい。クレアチンリン酸は筋肉内で高エネルギーリン酸結合を蓄えるエネルギー代謝物質であり、局所炎症の発現メディエーターではありません。"
        ]
    },
    {
        "qid": "f58c4f4af5742db7",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。プラスミンはフィブリンを分解する主要な線維素溶解（線溶）系酵素です。",
            "正しい。サブスタンスPは痛覚伝達や神経原性炎症（血管拡張等）に関与する神経ペプチドであり、直接の血液凝固・線溶系カスケード因子ではありません。",
            "間違い。プロトロンビンは凝固因子（第II因子）であり、トロンビンに変換されてフィブリノゲンをフィブリンにします。",
            "間違い。カルシウムイオンは血液凝固系（第IV因子）において凝固カスケードの多くの反応過程で必須の役割を果たします。"
        ]
    },
    {
        "qid": "d2c24d8495e6e332",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。樹状細胞（皮膚のランゲルハンス細胞など）は専門的な抗原提示細胞（APC）であり、灸刺激による微小炎症で生じた抗原・損傷関連分子を取り込んでT細胞に提示し、免疫系を活性化します。",
            "間違い。サプレッサーT細胞（制御性T細胞）は免疫応答を抑制・制御する細胞です。",
            "間違い。B細胞も抗原提示能を持ちますが、一次免疫応答における強力な抗原提示とT細胞活性化の主体は樹状細胞です。",
            "間違い。ヘルパーT細胞は抗原提示細胞から提示された抗原情報を受け取って他の免疫細胞を活性化する司令塔です。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2006/00_source/question_2006_7.json"

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

p10_file = P10_DIR / "question_2006_7_questionType_fixed.json"
p15_file = P15_DIR / "question_2006_7_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2006_7_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2006_7_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2006_7 successfully.")
