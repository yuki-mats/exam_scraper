import json
import os
from scripts.common.question_identity import review_question_id

source_path = 'output/anma/questions_json/2025/00_source/question_81010_7.json'
with open(source_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

questions = data.get('question_bodies', [])

p10 = []
p15 = []
p23 = []
p21 = []

explanations = {
    "e9dce18075570ef2": [ # Q151
        "正しい。結合織マッサージ（ディッケ法）特有の手技法は、指頭を用いて皮下結合組織を引く「擦過軽擦（ストローキング/ツーク手技）」である。",
        "間違い。縦行揉捏は一般的な西洋マッサージの手技である。",
        "間違い。牽引振せんは一般的な西洋マッサージや運動手技である。",
        "間違い。らせん状強擦は一般的な西洋マッサージの手技である。"
    ],
    "b25a5c8d4a322fee": [ # Q152
        "間違い。押圧に細かい振動を加えるのは「振動圧法」である。",
        "間違い。一点につき3〜5秒ずつ押圧するのは押圧三原則の「持続の圧」の基本時間である。",
        "正しい。緩圧法は圧の強度を第1圧（軽圧）、第2圧（中圧）、第3圧（快圧）のように段階的に区切って漸増・漸減させる押圧手技である。",
        "間違い。一定限度まで押圧後に急激に離すのは「衝圧法」である。"
    ],
    "7c11f1c1f5f2072d": [ # Q153
        "間違い。関節モビリゼーションは現代徒手理学療法の手技である。",
        "間違い。結合織マッサージはドイツのディッケが提唱した反射療法である。",
        "正しい。指圧療法は、日本の伝統的な按腹・手技療法に米国のオステオパシーやカイロプラクティックなどの理論・技術が導入されて体系化・成立した。",
        "間違い。足の反射療法は指圧法の直接的成立母体ではない。"
    ],
    "3b11922e4ab00a31": [ # Q154
        "間違い。Ⅰ群線維（Aα線維）は筋紡錘Ia線維や腱器官Ib線維からの深部感覚を伝える。",
        "正しい。振せん法による振動・触圧刺激は皮膚の機械受容器（パチニ小体やマイスネル小体等）で受容され、有髄の「Ⅱ群線維（Aβ線維）」を経て中枢へ伝導される。",
        "間違い。Ⅲ群線維（Aδ線維）は速い痛覚や冷覚を伝える。",
        "間違い。Ⅳ群線維（C線維）は遅い痛覚や温覚を伝える無髄線維である。"
    ],
    "89ce19ea5ff539c0": [ # Q155
        "正しい。粗大触圧覚は後角で二次ニューロンに交代し、交叉して「脊髄前索」の前脊髄視床路を上行して視床へ向かう。",
        "間違い。延髄後索核は後索（識別性触圧覚・深部感覚）の中継核である。",
        "間違い。内側毛帯は後索核からの二次ニューロンが交叉して視床へ上行する路である。",
        "間違い。外側膝状体は視覚伝導路の中継核である。"
    ],
    "24926ab82f21bd9b": [ # Q156
        "間違い。パチニ小体は真皮深層や皮下組織、骨膜などに分布する。",
        "正しい。メルケル盤（触板）は表皮の最深層（基底層）に局在する触圧覚受容器である。",
        "間違い。毛包受容器は真皮内の毛包周囲に分布する。",
        "間違い。ルフィニ終末は真皮深層や関節包などに分布する。"
    ],
    "13bebc8be5161c7e": [ # Q157
        "間違い。拮抗抑制は主動作筋の興奮に伴い拮抗筋の活動が抑制される反射である。",
        "正しい。筋腱移行部（ゴルジ腱器官）の圧迫刺激はIb求心性線維を介して同名筋のα運動ニューロンを抑制し筋緊張を緩和させる「自原抑制（Ib抑制）」を引き起こす。",
        "間違い。伸張反射は筋紡錘刺激により自己筋を反射的に収縮させる反射である。",
        "間違い。屈曲反射は侵害刺激に対して四肢を屈曲・回避させる多シナプス反射である。"
    ],
    "5c1ba582ebcad8d1": [ # Q158
        "間違い。転調作用は全身の反応性や体質を改善する作用である。",
        "間違い。誘導作用は他部位の充血・鬱血を血行誘導によって緩和する作用である。",
        "間違い。矯正作用は関節可動域や骨格の変位を徒手的に整える作用である。",
        "正しい。撮診点（内臓疾患に伴う体表反射帯）への手技刺激は、体性−内臓反射を介して対応する内臓機能を調節する「反射作用」を利用したものである。"
    ],
    "8a2bc98005d804b2": [ # Q159
        "正しい。ストレス刺激に対して視床下部からCRHが分泌され、これを受けて「下垂体前葉」から副腎皮質刺激ホルモン（ACTH）が分泌される。",
        "間違い。アドレナリンは副腎髄質から分泌される。",
        "間違い。コルチゾールは副腎皮質から分泌される。",
        "間違い。バソプレシンは下垂体後葉から分泌される。"
    ],
    "ae707edd48f61fc5": [ # Q160
        "間違い。緊急反応（交感神経興奮）では散瞳がみられる（縮瞳は副交感神経作用）。",
        "間違い。交感神経興奮により気管支平滑筋は弛緩・拡張する。",
        "正しい。キャノンの緊急反応では交感神経・副腎髄質系（SAM系）が活性化し、アドレナリン作用により肝臓でのグリコーゲン分解が促進されて「血糖値が上昇」する。",
        "間違い。交感神経興奮により消化管運動および消化管平滑筋は弛緩・抑制される。"
    ]
}

for i, q in enumerate(questions):
    qid = review_question_id(q)
    url = q.get('question_url', '')
    label = q.get('questionLabel', '')
    exam = q.get('examLabel', '')
    intent = q.get('questionIntent', 'select_correct')
    raw_correct = q.get('correctChoiceText', [])
    
    # 10_questionType
    p10.append({
        "original_question_id": qid,
        "questionType": "true_false",
        "isCalculationQuestion": False,
        "question_url": url
    })
    
    # 15_questionIntent
    p15.append({
        "original_question_id": qid,
        "questionIntent": intent,
        "question_url": url
    })
    
    # 23_correctChoiceText
    p23.append({
        "original_question_id": qid,
        "correctChoiceText": raw_correct,
        "question_url": url
    })
    
    # 21_explanationText
    exp_choices = explanations.get(qid)
    if not exp_choices:
        raise ValueError(f"Missing explanation for {qid}")
    
    p21.append({
        "original_question_id": qid,
        "public_question_id": qid,
        "question_url": url,
        "questionLabel": label,
        "examLabel": exam,
        "source_filepath": "output/anma/questions_json/2025/00_source/question_81010_7.json",
        "explanationText": exp_choices,
        "suggestedQuestionDetailsByChoice": [],
        "questionLearningPatternId": "principles_exceptions",
        "isLawRelated": False,
        "lawGroundedExplanationNotNeeded": True,
        "lawReferences": [
            [],
            [],
            [],
            []
        ]
    })

def write_patch(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

write_patch('output/anma/questions_json/2025/10_questionType_fixed/question_81010_7_questionType_fixed.json', p10)
write_patch('output/anma/questions_json/2025/15_correctChoiceText_fixed/question_81010_7_correctChoiceText_fixed.json', p15)
write_patch('output/anma/questions_json/2025/23_correctChoiceText_fixed/question_81010_7_correctChoiceText_fixed.json', p23)
write_patch('output/anma/questions_json/2025/21_explanationText_added/question_81010_7_explanationText_added.json', p21)

print(f"Successfully generated patches for {len(questions)} questions in question_81010_7")
