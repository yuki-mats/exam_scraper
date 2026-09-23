# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2006 question_2006_5.json (Q101 - Q125)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2006/00_source/question_2006_5.json"

P10_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/10_questionType_fixed"
P15_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/15_correctChoiceText_fixed"
P23_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/23_correctChoiceText_fixed"
P21_DIR = BASE_DIR / "output/shinkyu/questions_json/2006/21_explanationText_added"

for p in [P10_DIR, P15_DIR, P23_DIR, P21_DIR]:
    p.mkdir(parents=True, exist_ok=True)

with open(SOURCE_FILE, "r", encoding="utf-8") as f:
    source_data = json.load(f)
    source_questions = source_data.get("question_bodies", source_data)

# Q101 - Q125 definition
# 101: 49a9e68bf85d913e (湿邪 -> 2)
# 102: 2883d5a2f5d1dd30 (消穀善飢 -> 3)
# 103: d166256c81d72219 (肝血虚 -> 1)
# 104: c4f475699d48fb9f (半表半裏証で見られないもの: 悪風 -> 3)
# 105: b274084dba87197c (腰痛・季肋部脹満・顔色青黒い -> 肝経病証: 3)
# 106: 946b3f948c066ba4 (五臓と病態で誤り: 肝───汗をよくかく -> 1)
# 107: a42f9ccf89c283a7 (六経病証の病位で誤り: 太陰───背面の裏 -> 4)
# 108: b763419f3a2c3e09 (十二刺で筋痺: 恢刺 -> 4)
# 109: 55a2555a1b59b5a9 (五刺と組織で正しい: 輸刺───骨 -> 1)
# 110: 07dcec24494fa75b (大椎から肘頭の骨度: 1尺7寸 -> 3)
# 111: c9834c3f217d6d62 (手の陽明大腸経: 前腕部で橈骨神経沿い -> 3)
# 112: 33db783ec0581c1e (奇経八脈: 衝脈は子宮から起こる -> 1)
# 113: d7390ead68ab0ed6 (内関穴: 心包経の絡穴 -> 4)
# 114: 9e8b8d5877cd0cfb (肩背部の経穴と神経で誤り: 肩貞───肩甲上神経 -> 3)
# 115: f1c4b2ab2e05b5bc (伏在神経支配領域にない: 血海 -> 1)
# 116: dbf9e81b89597ef8 (前鋸筋上にない: 食竇 -> 4)
# 117: 714a6467adddf8c9 (取穴法で誤り: 曲垣は肩甲骨上角直上 -> 4)
# 118: 490c63e13cefb708 (取穴法で正しい: 巨髎は瞳孔線上鼻孔外方8分 -> 1)
# 119: c8085ef3649f8de1 (第1中足指節関節後内側陥凹: 太白 -> 2)
# 120: 126de2ab18995a7b (直立下垂手掌大腿外側中指先端下際: 風市 -> 2)
# 121: 1dbb20b953ca93cd (任脈上に募穴がない: 脾経 -> 2)
# 122: 19a749015c548380 (経火穴と栄水穴: 支溝───液門 -> 1)
# 123: 52fe56026f429e82 (虚寒証に対する刺法で不適切: 吸気時刺入・呼気時抜鍼 -> 2)
# 124: e17eccd4b49fd117 (前腕外側手背痛・頭部後屈増悪・腕橈骨筋反射減弱: C5-C6間と合谷 -> 2)
# 125: eccfb625203e12de (筋と経穴: 菱形筋───大杼 -> 4)

questions_data = [
    {
        "qid": "49a9e68bf85d913e",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。風邪は軽揚性・開泄性・善行数変を特徴とし、動きが速く変化しやすい性質を持ちます。",
            "正しい。湿邪は重濁性・粘滞性を特徴とし、身体が重だるく動きが遅くなり、症状が停滞しやすい性質を持ちます。",
            "間違い。燥邪は乾渋性を特徴とし、津液を損傷して乾燥症状を引き起こします。",
            "間違い。火邪（熱邪）は炎上性・燔灼性を特徴とし、熱感や発赤などの急速な炎症症状をもたらします。"
        ]
    },
    {
        "qid": "2883d5a2f5d1dd30",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。食欲不振は脾胃の気虚や湿邪の停滞などで生じます。",
            "間違い。曖気（げっぷ）は胃気上逆などで生じます。",
            "正しい。胃熱では胃の受納・腐熟機能が異常に亢進するため、食べてもすぐに空腹感を覚える消穀善飢がみられます。",
            "間違い。味覚減退は脾気虚や湿盛などで生じます。"
        ]
    },
    {
        "qid": "d166256c81d72219",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。肝血虚では筋膜や頭目を滋養できなくなるため、血虚生風により四肢のふるえやめまい、視力減退などが現れます。",
            "間違い。肝陽上亢では頭痛、めまい、顔面紅潮、いらいらなどがみられます。",
            "間違い。肝気鬱結では胸脇部脹痛、ため息、情緒不安定などがみられます。",
            "間違い。肝火上炎では激しい頭痛、耳鳴り、目赤、口苦などがみられます。"
        ]
    },
    {
        "qid": "c4f475699d48fb9f",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。往来寒熱は半表半裏証（少陽病）の典型的な症状です。",
            "間違い。胸脇苦満は邪気が半表半裏にあることで生じる代表的な症候です。",
            "正しい。悪風は表証（太陽中風など）にみられる症状であり、病邪が表から半表半裏に入った半表半裏証ではみられません。",
            "間違い。口苦は少陽病の熱証に伴う代表的な症候です。"
        ]
    },
    {
        "qid": "b274084dba87197c",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。心包経病証では掌の熱感、前腕痛、心痛、心煩などがみられます。",
            "間違い。胆経病証では口苦、ため息、脇痛、頭痛（側頭部）などがみられます。",
            "正しい。肝経病証（足の厥陰肝経）では腰痛で前屈・後屈ができない、季肋部脹満、顔色青黒いなどの症候が現れます。",
            "間違い。脾経病証では胃脘部痛、腹脹、下痢、身体の重だるさなどがみられます。"
        ]
    },
    {
        "qid": "946b3f948c066ba4",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "正しい", "正しい", "正しい"],
        "explanations": [
            "間違い。汗は「心の液」であり、汗をよくかく病態は心や衛気に関連します。肝の液は「涙」です。",
            "正しい。心は喜をつかさどり、心の病変では過度に笑う症状（喜笑不休など）がみられます。",
            "正しい。脾は思をつかさどり、脾の病変では思い悩む精神症状がみられます。",
            "正しい。肺は悲・憂をつかさどり、肺の病変では悲しむ症状がみられます。"
        ]
    },
    {
        "qid": "a42f9ccf89c283a7",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。太陽病の病位は背面の表です。",
            "正しい。少陽病の病位は側面の半表半裏です。",
            "正しい。少陰病の病位は背面の裏です。",
            "間違い。太陰病の病位は「腹面の裏」です。背面の裏は少陰病に該当します。"
        ]
    },
    {
        "qid": "b763419f3a2c3e09",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。陰刺は左右両側の同名経穴に刺入する刺法で、寒厥（冷え）などに用います。",
            "間違い。関刺は関節付近の腱に刺入する刺法で、筋痺（腱のひきつれなど）に用いますが直刺します。",
            "間違い。短刺は骨の近くまで深く刺し鍼を揺り動かす刺法で、骨痺に用います。",
            "正しい。恢刺は筋腱の傍らに刺入し、前後左右に鍼を動かして筋膜を緩める刺法で、筋痺に用います。"
        ]
    },
    {
        "qid": "55a2555a1b59b5a9",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。輸刺は骨まで深く刺入して抜鍼する刺法で、骨病（腎に応じる）に用います。",
            "間違い。半刺は皮毛に浅く刺して素早く抜く刺法で、皮病（肺に応じる）に用います。筋病は関刺などです。",
            "間違い。合谷刺は筋肉の深部に左右斜めに刺入する刺法で、肉病（脾に応じる）に用います。脈病は豹文刺です。",
            "間違い。豹文刺は脈絡の鬱血部を細かく刺して瀉血する刺法で、脈病（心に応じる）に用います。皮病は半刺です。"
        ]
    },
    {
        "qid": "07dcec24494fa75b",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。1尺3寸は大椎から肩峰までの骨度（7寸）と肩峰から肘頭までの骨度（1尺）の合計に一致しません。",
            "間違い。1尺5寸は不適切です。",
            "正しい。大椎から肩峰までは7寸、肩峰から肘頭までは1尺（10寸）であるため、大椎から肘頭までの骨度は合計で1尺7寸（17寸）となります。",
            "間違い。1尺9寸は不適切です。"
        ]
    },
    {
        "qid": "c9834c3f217d6d62",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。尺骨神経沿いを走行するのは手の太陽小腸経および手の少陰心経です。",
            "間違い。正中神経沿いを走行するのは手の厥陰心包経です。",
            "正しい。手の陽明大腸経は前腕部において橈骨神経（浅枝）の走行に沿って走ります。",
            "間違い。筋皮神経沿いを走行するのは上腕部での一部であり、前腕部の大腸経走行部位ではありません。"
        ]
    },
    {
        "qid": "33db783ec0581c1e",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。衝脈は胞中（子宮）から起こり、会陰から腹部を上行して「十二経の海」「血海」と呼ばれます。",
            "間違い。帯脈は第11肋骨先端下（章門・帯脈付近）から起こり、腰部を帯状に周回します。長強から起こるのは督脈です。",
            "間違い。陽蹻脈は足外顆下（申脈）から起こります。後頚部から起こるわけではありません。",
            "間違い。陰維脈は内果上方（築賓）から起こり諸陰経を維持・連絡します。胸部から起こるわけではありません。"
        ]
    },
    {
        "qid": "d7390ead68ab0ed6",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。内関は手関節掌側横紋の上方2寸にあり、手関節掌側横紋上にあるのは大陵（心包経の原穴・兪土穴）です。",
            "間違い。内関は長掌筋腱と橈側手根屈筋腱の間に取穴します。腕橈骨筋と橈側手根屈筋腱の間にあるのは列欠（肺経）です。",
            "間違い。内関は八脈交会穴で「陰維脈」に通じます（八脈交会穴：内関−陰維脈）。陽蹻脈に通じるのは申脈です。",
            "正しい。内関は手の厥陰心包経の絡穴であり、手少陽三焦経に連絡します。"
        ]
    },
    {
        "qid": "9e8b8d5877cd0cfb",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。天宗は棘下筋部に位置し、肩甲上神経が分布します。",
            "正しい。秉風は棘上筋部に位置し、肩甲上神経が分布します。",
            "間違い。肩貞は腋窩横紋後端の上方1寸、小円筋や大円筋付近にあり、腋窩神経や肩甲下神経が分布します。肩甲上神経ではありません。",
            "正しい。臑兪は肩甲棘三角の下縁、小円筋部・三角筋部に位置し、腋窩神経および肩甲上神経が関与します。"
        ]
    },
    {
        "qid": "f1c4b2ab2e05b5bc",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。血海は大腿前内側（膝蓋骨内上角の上方2寸）に位置し、大腿神経（前皮枝・筋枝）の支配領域にあります（伏在神経は下腿内側に分布します）。",
            "間違い。陰陵泉は脛骨内側顆下縁に位置し、伏在神経（大腿神経皮枝）の分布領域にあります。",
            "間違い。地機は陰陵泉の下方3寸に位置し、伏在神経支配領域にあります。",
            "間違い。中都は内果尖の上方7寸（脛骨内側面上）に位置し、伏在神経支配領域にあります。"
        ]
    },
    {
        "qid": "dbf9e81b89597ef8",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。淵腋は第4肋間・前正中線の外方6寸にあり、前鋸筋上に位置します。",
            "間違い。大包は第6肋間・前正中線の外方6寸にあり、前鋸筋上に位置します。",
            "間違い。輒筋は第4肋間・淵腋の前方1寸にあり、前鋸筋上に位置します。",
            "正しい。食竇は第5肋間・前正中線の外方4寸にあり、大胸筋下縁または外腹斜筋・肋間筋上に位置し、前鋸筋上にはありません。"
        ]
    },
    {
        "qid": "714a6467adddf8c9",
        "intent": "select_incorrect",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。肩中兪は第7頸椎棘突起下縁（大椎）の外方2寸に取穴するため、正しい記述です。",
            "間違い。肩外兪は第1胸椎棘突起下縁（陶道）の外方3寸に取穴するため、正しい記述です。",
            "間違い。天窓は胸鎖乳突筋の後縁で下顎角と同じ高さ（扶突の後方）に取穴するため、正しい記述です。",
            "正しい。曲垣は「肩甲棘内側端の上際陥凹部」に取穴します。肩甲骨上角直上は肩中兪や肩井などの指標であり、曲垣の取穴法として誤りです。"
        ]
    },
    {
        "qid": "490c63e13cefb708",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。巨髎は瞳孔線上、鼻孔下縁と同じ高さ（鼻孔外方約8分）の陥凹部に取穴します。",
            "間違い。地倉は口角の外方4分に取穴します。瞳孔線上ではありません（巨髎より内側）。",
            "間違い。承泣は瞳孔の直下、眼窩下縁と眼球の間に取穴します。瞳孔内側ではありません。",
            "間違い。迎香は鼻翼外縁中央の外方5分（鼻唇溝中）に取穴します。鼻根外方にあるのは晴明などです。"
        ]
    },
    {
        "qid": "c8085ef3649f8de1",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。大都は第1中足指節関節の「前内側」陥凹部に取穴します。",
            "正しい。太白は足の太陰脾経の原穴・兪土穴であり、第1中足指節関節の「後内側」陥凹部（赤白肉際）に取穴します。",
            "間違い。公孫は第1中足骨底の前下縁陥凹部に取穴します。",
            "間違い。然谷は足の少陰腎経の栄水穴であり、舟状骨粗面の下前方に取穴します。"
        ]
    },
    {
        "qid": "126de2ab18995a7b",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。中涜は風市の下方2寸（膝蓋骨外上角の上方5寸）に取穴します。",
            "正しい。風市は直立して腕を自然に下垂したとき、大腿外側で中指の先端が当たるところ（膝蓋骨外上角の上方7寸）に取穴します。",
            "間違い。膝陽関は陽陵泉の上方3寸、大腿二頭筋腱と腸脛靭帯の間の陥凹部に取穴します。",
            "間違い。陽陵泉は腓骨頭の前下部陥凹部に取穴します。"
        ]
    },
    {
        "qid": "1dbb20b953ca93cd",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。胃経の募穴は中脘であり、任脈上にあります。",
            "正しい。脾経の募穴は「章門」であり、足の厥陰肝経（第11肋骨先端下）上に位置するため、任脈上にはありません。",
            "間違い。肺経の募穴は中府（肺経）ですが、心包経（膻中）、心経（巨闕）、小腸経（関元）、膀胱経（中極）、三焦経（石門）など多くの募穴が任脈上にあります。選択肢の中で脾経の募穴（章門）は肝経上に位置します。",
            "間違い。大腸経の募穴は天枢（胃経）ですが、問題の選択肢の中で脾経の募穴章門（肝経）が任脈上にない代表例として問われています。"
        ]
    },
    {
        "qid": "19a749015c548380",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。手少陽三焦経において、支溝は経火穴であり、液門は栄水穴です。",
            "間違い。前谷は手太陽小腸経の栄水穴、小海は合土穴です。",
            "間違い。間使は手厥陰心包経の経金穴、労宮は栄火穴です。",
            "間違い。経渠は手太陰肺経の経金穴、魚際は栄火穴です。"
        ]
    },
    {
        "qid": "52fe56026f429e82",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。徐々に刺入し速やかに抜鍼する刺法（徐刺疾抜）は補法であり、虚寒証に適しています。",
            "間違い。吸気時に刺入し呼気時に抜鍼する刺法は「瀉法」です。虚寒証に対する補法としては「呼気時に刺入し吸気時に抜鍼する」のが適切です。",
            "正しい。経脈の流注方向に沿って斜刺する刺法（迎随の補法）は補法であり、虚寒証に適しています。",
            "正しい。抜鍼後ただちに刺鍼孔を押さえて閉じる刺法（開闔の補法）は補法であり、虚寒証に適しています。"
        ]
    },
    {
        "qid": "e17eccd4b49fd117",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。C4-C5間（C5神経根症）では肩外側部の感覚障害や三角筋・上腕二頭筋反射の異常がみられます。",
            "正しい。前腕外側・母指〜示指（手背）の疼痛・しびれ、頸部後屈での増悪（スパーリング徴候陽性）、腕橈骨筋反射減弱は「C6神経根症（C5-C6椎間孔障害）」の特徴です。罹患神経根の障害高位（C5-C6間）および支配領域の合谷への局所治療が適切です。",
            "間違い。C6-C7間（C7神経根症）では示指・中指の感覚障害や上腕三頭筋反射減弱がみられます。",
            "間違い。C7-Th1間（C8神経根症）では環指・小指や前腕内側の感覚障害がみられます。"
        ]
    },
    {
        "qid": "eccfb625203e12de",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。肩外兪は第1胸椎棘突起下外方3寸にあり、深部には肩甲挙筋や菱形筋が存在します。前鋸筋ではありません。",
            "間違い。天宗は棘下窩にあり、棘下筋上に位置します。大菱形筋ではありません。",
            "間違い。附分は第2胸椎棘突起下外方3寸にあり、僧帽筋、菱形筋、脊柱起立筋上に位置します。肩甲挙筋ではありません。",
            "正しい。大杼は第1胸椎棘突起下外方1寸5分にあり、僧帽筋深部の菱形筋（小菱形筋・大菱形筋上部）および脊柱起立筋上に位置します。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2006/00_source/question_2006_5.json"

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

p10_file = P10_DIR / "question_2006_5_questionType_fixed.json"
p15_file = P15_DIR / "question_2006_5_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2006_5_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2006_5_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2006_5 successfully.")
