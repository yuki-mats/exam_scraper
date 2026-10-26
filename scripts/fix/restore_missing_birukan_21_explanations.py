import json
import os

# 1. 95001 File 1 問25
path_95001_1 = 'output/birukan/questions_json/95001/21_explanationText_added/question_95001_1_merged_explanationText_added.json'
with open(path_95001_1, 'r', encoding='utf-8') as f:
    d_95001_1 = json.load(f)

# check if q25 already exists
if not any(q.get('questionLabel') == '問25' for q in d_95001_1):
    q25 = {
        "questionBodyText": "冬季における暖房時の留意事項の組合せとして、最も適当なものは次のうちどれか。\nア　　床上0.1mと1.1mの温度差を、3°C以下とする。\nイ　　気流は、1.5m／s 程度に保つ。\nウ　　ウォームビズの導入によりCO₂排出量を削減できる。\n工　　低湿度では、呼吸器疾患に罹患しやすい。",
        "examLabel": "第48回（平成30年度（2018年）） 建築物の環境衛生",
        "questionLabel": "問25",
        "choiceTextList": [
            "アとイとウとエ",
            "アとウとエ",
            "アとエ",
            "イとウとエ",
            "ウとエ"
        ],
        "originalQuestionChoiceImageUrls": [[], [], [], [], []],
        "category": None,
        "examYear": 2018,
        "examOccurrenceId": "2018-r48",
        "list_group_id": "95001",
        "question_url": "https://birukan.kakomonn.com/questions/64469",
        "public_question_id": "42017c163345e810",
        "original_question_id": "42017c163345e810",
        "source_question_id": "birukan:kakomonn:questions:64469",
        "source_public_question_id": "28d083a05e24d452",
        "questionSourceSite": "kakomonn",
        "canonical_question_key": "birukan:2018-r48:kankyo-eisei:q025",
        "question_id_policy_key": "canonical-question-key:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:{exam_occurrence_id_or_year}:q{question_number}",
        "sourceUniqueKeys": [
            "birukan:2018-r48:kankyo-eisei:q025:s01",
            "birukan:2018-r48:kankyo-eisei:q025:s02",
            "birukan:2018-r48:kankyo-eisei:q025:s03",
            "birukan:2018-r48:kankyo-eisei:q025:s04",
            "birukan:2018-r48:kankyo-eisei:q025:s05"
        ],
        "questionImageStorageUrls": [],
        "questionIntent": "select_correct",
        "correctChoiceText": [
            "間違い",
            "正しい",
            "間違い",
            "間違い",
            "間違い"
        ],
        "explanation_common_prefix": [],
        "explanation_common_prefix_inferred_correct_choice": 2,
        "explanation_common_summary": [],
        "explanation_choice_snippets": [],
        "explanation_choice_correctness": [],
        "answer_result_text": "正解は 2 です。",
        "answer_result_inferred_correct_choice_numbers": [2],
        "questionType": "true_false",
        "isCalculationQuestion": False,
        "isLawRelated": False,
        "lawGroundedExplanationNotNeeded": True,
        "lawReferences": [],
        "lawContextForExplanation": None,
        "sourceQuestionKey": "birukan:95001:42017c163345e810",
        "reviewQuestionId": "42017c163345e810",
        "sourceRecordRef": "question_95001_1.json#24",
        "explanationReferences": [],
        "explanationText": [
            "間違い。記述ア、ウ、エはいずれも適切ですが、記述イが不適切であるため誤りです。",
            "正しい。ア：床上0.1m（足元）と1.1m（着座時の頭部）の垂直温度差は不快感を防ぐため3℃以下とすることが推奨されます。ウ：ウォームビズの推進は暖房エネルギーとCO2排出量の削減に寄与します。エ：低湿度環境では気道粘膜の乾燥やウイルスの活性化により呼吸器疾患の罹患リスクが高まります。これらア・ウ・エの組合せは適切です。",
            "間違い。記述ウ（ウォームビズによるCO2排出量削減）も適切な留意事項であるため、アとエのみとする本肢は誤りです。",
            "間違い。記述イ（気流を1.5m/s程度に保つ）は過大であり不適切です。冬季暖房時の室内気流はドラフト感を避けるため0.15m/s〜0.2m/s以下に保つことが望ましいとされています。",
            "間違い。記述ア（床上0.1mと1.1mの温度差を3℃以下とする）も適切な留意事項であるため、ウとエのみとする本肢は誤りです。"
        ],
        "lawRevisionFacts": [],
        "questionLearningPatternId": None,
        "suggestedQuestionDetailsByChoice": []
    }
    d_95001_1.append(q25)
    with open(path_95001_1, 'w', encoding='utf-8') as f:
        json.dump(d_95001_1, f, ensure_ascii=False, indent=2)
    print("Added q25 to 95001 File 1")

# 2. 95001 File 7 問175
path_95001_7 = 'output/birukan/questions_json/95001/21_explanationText_added/question_95001_7_merged_explanationText_added.json'
with open(path_95001_7, 'r', encoding='utf-8') as f:
    d_95001_7 = json.load(f)

if not any(q.get('questionLabel') == '問175' for q in d_95001_7):
    q175 = {
        "questionBodyText": "殺鼠剤やその剤型に関する次の記述のうち、最も不適当なものはどれか。",
        "examLabel": "第48回（平成30年度（2018年）） ねずみ、昆虫等の防除",
        "questionLabel": "問175",
        "choiceTextList": [
            "ジフェチアロールは、ワルファリンに対する抵抗性を獲得したネズミに対しても有効である。",
            "クマテトラリルは、第2世代の抗凝血性殺鼠剤である。",
            "シリロシドは、急性殺鼠剤である。",
            "粉剤は、餌材料にまぶして毒餌を作製するのに使用することができる。",
            "ブロマジオロン製剤は、建築物衛生法に基づく特定建築物内では使用できない。"
        ],
        "originalQuestionChoiceImageUrls": [[], [], [], [], []],
        "category": None,
        "examYear": 2018,
        "examOccurrenceId": "2018-r48",
        "list_group_id": "95001",
        "question_url": "https://birukan.kakomonn.com/questions/64619",
        "public_question_id": "beb20d320a25c6ba",
        "original_question_id": "beb20d320a25c6ba",
        "source_question_id": "birukan:kakomonn:questions:64619",
        "source_public_question_id": "6ad583dcd494fd76",
        "questionSourceSite": "kakomonn",
        "canonical_question_key": "birukan:2018-r48:nezumi-konchu:q175",
        "question_id_policy_key": "canonical-question-key:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:{exam_occurrence_id_or_year}:q{question_number}",
        "sourceUniqueKeys": [
            "birukan:2018-r48:nezumi-konchu:q175:s01",
            "birukan:2018-r48:nezumi-konchu:q175:s02",
            "birukan:2018-r48:nezumi-konchu:q175:s03",
            "birukan:2018-r48:nezumi-konchu:q175:s04",
            "birukan:2018-r48:nezumi-konchu:q175:s05"
        ],
        "questionImageStorageUrls": [],
        "questionIntent": "select_incorrect",
        "correctChoiceText": [
            "正しい",
            "間違い",
            "正しい",
            "正しい",
            "正しい"
        ],
        "explanation_common_prefix": [],
        "explanation_common_prefix_inferred_correct_choice": 2,
        "explanation_common_summary": [],
        "explanation_choice_snippets": [],
        "explanation_choice_correctness": [],
        "answer_result_text": "正解は 2 です。",
        "answer_result_inferred_correct_choice_numbers": [2],
        "questionType": "true_false",
        "isCalculationQuestion": False,
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [],
        "lawContextForExplanation": "選択肢5は建築物衛生法施行規則第4条の5第2項第2号が判断軸。特定建築物で使う殺そ剤は承認済みの医薬品又は医薬部外品でなければならず、ブロマジオロン製剤の一律使用禁止までは同項から導けないため、製品ごとの承認区分確認が必要。出題時正答との整合は03bで監査する。選択肢1〜4は成分分類・剤型に関する技術的論点として法令根拠を付さない。",
        "sourceQuestionKey": "birukan:95001:beb20d320a25c6ba",
        "reviewQuestionId": "beb20d320a25c6ba",
        "sourceRecordRef": "question_95001_7.json#24",
        "explanationReferences": [],
        "explanationText": [
            "正しい。ジフェチアロールは第2世代のクマリン系抗凝血性殺鼠剤であり、ワルファリン等の第1世代薬剤に対して抵抗性を獲得したスーパーラットに対しても高い殺鼠効力を示します。",
            "間違い。正しくは、クマテトラリルは第1世代の抗凝血性殺鼠剤です。ワルファリンと同様に複数回摂取（累積毒）によって効果を発揮する系統であり、単回経口投与で効力を示す第2世代抗凝血性殺鼠剤ではありません。",
            "正しい。シリロシド（赤色海葱配糖体）は急性殺鼠剤（速効性殺鼠剤）に分類され、中枢神経系や心臓に作用して速やかに致死効果をもたらします。",
            "正しい。粉剤はそのまま散布するだけでなく、穀類や油脂等の餌材料に均一にまぶして毒餌（毒団子等）を調製する用途に適しています。",
            "正しい。特定建築物で使用する殺鼠剤は建築物衛生法施行規則に基づき承認を受けた医薬品又は医薬部外品に限られます。ブロマジオロン製剤は特定建築物用として承認されていないため使用できません。"
        ],
        "lawRevisionFacts": [],
        "questionLearningPatternId": None,
        "suggestedQuestionDetailsByChoice": []
    }
    d_95001_7.append(q175)
    with open(path_95001_7, 'w', encoding='utf-8') as f:
        json.dump(d_95001_7, f, ensure_ascii=False, indent=2)
    print("Added q175 to 95001 File 7")

# 3. 95005 File 5 問104
path_95005_5 = 'output/birukan/questions_json/95005/21_explanationText_added/question_95005_5_merged_explanationText_added.json'
with open(path_95005_5, 'r', encoding='utf-8') as f:
    d_95005_5 = json.load(f)

if not any(q.get('questionLabel') == '問104' for q in d_95005_5):
    q104 = {
        "questionBodyText": "建築基準法及びその施行令に関する用語に該当する内容の組合せとして、正しいものは次のうちどれか。2つ選べ。",
        "examLabel": "第52回（令和4年度（2022年）） 建築物の構造概論",
        "questionLabel": "問104",
        "choiceTextList": [
            "「建築物」に該当：建築物に付属する門 「特殊建築物」に該当：事務所 「構造耐力上主要な部分」に該当：柱",
            "「建築物」に該当：鉄道線路敷地内の跨（こ）線橋 「特殊建築物」に該当：病院 「構造耐力上主要な部分」に該当：屋根",
            "「建築物」に該当：屋根のない観覧場 「特殊建築物」に該当：学校 「構造耐力上主要な部分」に該当：基礎",
            "「建築物」に該当：駅舎のプラットホーム上家 「特殊建築物」に該当：倉庫 「構造耐力上主要な部分」に該当：壁",
            "「建築物」に該当：地下工作物内の施設 「特殊建築物」に該当：共同住宅 「構造耐力上主要な部分」に該当：床"
        ],
        "originalQuestionChoiceImageUrls": [[], [], [], [], []],
        "category": None,
        "examYear": 2022,
        "examOccurrenceId": "2022-r52",
        "list_group_id": "95005",
        "question_url": "https://birukan.kakomonn.com/questions/69652",
        "public_question_id": "23b04b1e7550764b",
        "original_question_id": "23b04b1e7550764b",
        "source_question_id": "birukan:kakomonn:questions:69652",
        "source_public_question_id": "86ad899867aa4542",
        "questionSourceSite": "kakomonn",
        "canonical_question_key": "birukan:2022-r52:kozo-gairon:q104",
        "question_id_policy_key": "canonical-question-key:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:{exam_occurrence_id_or_year}:q{question_number}",
        "sourceUniqueKeys": [
            "birukan:2022-r52:kozo-gairon:q104:s01",
            "birukan:2022-r52:kozo-gairon:q104:s02",
            "birukan:2022-r52:kozo-gairon:q104:s03",
            "birukan:2022-r52:kozo-gairon:q104:s04",
            "birukan:2022-r52:kozo-gairon:q104:s05"
        ],
        "questionImageStorageUrls": [],
        "questionIntent": "select_correct",
        "correctChoiceText": [
            "間違い",
            "間違い",
            "正しい",
            "間違い",
            "正しい"
        ],
        "explanation_common_prefix": [],
        "explanation_common_prefix_inferred_correct_choice": 3,
        "explanation_common_summary": [],
        "explanation_choice_snippets": [],
        "explanation_choice_correctness": [],
        "answer_result_text": "正解は 3 と 5 です。",
        "answer_result_inferred_correct_choice_numbers": [3, 5],
        "questionType": "true_false",
        "isCalculationQuestion": False,
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [],
        "lawContextForExplanation": None,
        "sourceQuestionKey": "birukan:95005:23b04b1e7550764b",
        "reviewQuestionId": "23b04b1e7550764b",
        "sourceRecordRef": "question_95005_5.json#3",
        "explanationReferences": [],
        "explanationText": [
            "間違い。事務所は建築基準法第2条第2号に定める特殊建築物には該当しません（一般的な建築物です）。建築物に附属する門は建築物に該当し、柱は構造耐力上主要な部分に該当します。",
            "間違い。鉄道線路敷地内の跨線橋やプラットホーム上家は建築基準法第2条第1号ただし書により建築物から除外されます。また、屋根そのものは仕上げ材等を含み、構造耐力上主要な部分としては「屋根版」と規定されています。",
            "正しい。建築基準法第2条第1号により観覧のための工作物は屋根の有無にかかわらず建築物に含まれ、学校は同条第2号の特殊建築物に該当し、基礎は同法施行令第1条第3号の構造耐力上主要な部分に該当します。",
            "間違い。駅舎のプラットホーム上家は建築基準法第2条第1号ただし書により建築物から除外されます。倉庫は特殊建築物に該当し、壁（耐力壁）は構造耐力上主要な部分に該当します。",
            "正しい。地下工作物内に設ける事務所・店舗等の施設は建築基準法第2条第1号により建築物に該当し、共同住宅は同条第2号の特殊建築物に該当し、床（床版）は同法施行令第1条第3号の構造耐力上主要な部分に該当します。"
        ],
        "lawRevisionFacts": [],
        "questionLearningPatternId": None,
        "suggestedQuestionDetailsByChoice": []
    }
    d_95005_5.append(q104)
    with open(path_95005_5, 'w', encoding='utf-8') as f:
        json.dump(d_95005_5, f, ensure_ascii=False, indent=2)
    print("Added q104 to 95005 File 5")

# 4. 95007 File 1 問12
path_95007_1 = 'output/birukan/questions_json/95007/21_explanationText_added/question_95007_1_merged_explanationText_added.json'
with open(path_95007_1, 'r', encoding='utf-8') as f:
    d_95007_1 = json.load(f)

if not any(q.get('questionLabel') == '問12' for q in d_95007_1):
    q12 = {
        "questionBodyText": "建築物衛生法に基づく事業の登録に必要な物的要件のうち、機械器具以外の設備を必要としない登録事業は次のうちどれか。",
        "examLabel": "第54回（令和6年度（2024年）） 建築物衛生行政概論",
        "questionLabel": "問12",
        "choiceTextList": [
            "建築物清掃業",
            "建築物飲料水水質検査業",
            "建築物飲料水貯水槽清掃業",
            "建築物排水管清掃業",
            "建築物ねずみ昆虫等防除業"
        ],
        "originalQuestionChoiceImageUrls": [[], [], [], [], []],
        "category": None,
        "examYear": 2024,
        "examOccurrenceId": "2024-r54",
        "list_group_id": "95007",
        "question_url": "https://birukan.kakomonn.com/questions/77864",
        "public_question_id": "2db428387ea3146e",
        "original_question_id": "2db428387ea3146e",
        "source_question_id": "birukan:kakomonn:questions:77864",
        "source_public_question_id": "83cf92a014902b48",
        "questionSourceSite": "kakomonn",
        "canonical_question_key": "birukan:2024-r54:eisei-gyosei:q012",
        "question_id_policy_key": "canonical-question-key:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:{exam_occurrence_id_or_year}:q{question_number}",
        "sourceUniqueKeys": [
            "birukan:2024-r54:eisei-gyosei:q012:s01",
            "birukan:2024-r54:eisei-gyosei:q012:s02",
            "birukan:2024-r54:eisei-gyosei:q012:s03",
            "birukan:2024-r54:eisei-gyosei:q012:s04",
            "birukan:2024-r54:eisei-gyosei:q012:s05"
        ],
        "questionImageStorageUrls": [],
        "questionIntent": "select_correct",
        "correctChoiceText": [
            "正しい",
            "間違い",
            "間違い",
            "間違い",
            "間違い"
        ],
        "explanation_common_prefix": [],
        "explanation_common_prefix_inferred_correct_choice": 1,
        "explanation_common_summary": [],
        "explanation_choice_snippets": [],
        "explanation_choice_correctness": [],
        "answer_result_text": "正解は 1 です。",
        "answer_result_inferred_correct_choice_numbers": [1],
        "questionType": "true_false",
        "isCalculationQuestion": False,
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [],
        "lawContextForExplanation": None,
        "sourceQuestionKey": "birukan:95007:2db428387ea3146e",
        "reviewQuestionId": "2db428387ea3146e",
        "sourceRecordRef": "question_95007_1.json#11",
        "explanationReferences": [],
        "explanationText": [
            "正しい。建築物清掃業（1号登録）の登録基準における物的要件は真空掃除機や床みがき機等の機械器具のみであり、保管庫や実験室等の設備要件は定められていません。",
            "間違い。建築物飲料水水質検査業（2号登録）は、水質検査を適正に実施するための検査室（設備）の設置が物的要件として義務付けられています。",
            "間違い。建築物飲料水貯水槽清掃業（3号登録）は、揚水ポンプや高圧洗浄機等の器具に加え、器具や消毒薬剤を衛生的に保管・乾燥させる専用保管庫（設備）が必要です。",
            "間違い。建築物排水管清掃業（4号登録）は、高圧洗浄機や排水管清掃機に加え、器材を衛生的に保管・整備するための専用保管庫（設備）が必要です。",
            "間違い。建築物ねずみ昆虫等防除業（7号登録）は、防除用機器類に加え、殺虫剤・殺鼠剤等の毒劇物や薬剤を安全に施錠保管できる保管庫（設備）が必要です。"
        ],
        "lawRevisionFacts": [],
        "questionLearningPatternId": None,
        "suggestedQuestionDetailsByChoice": []
    }
    d_95007_1.append(q12)
    with open(path_95007_1, 'w', encoding='utf-8') as f:
        json.dump(d_95007_1, f, ensure_ascii=False, indent=2)
    print("Added q12 to 95007 File 1")

print("All missing explanations successfully restored.")
