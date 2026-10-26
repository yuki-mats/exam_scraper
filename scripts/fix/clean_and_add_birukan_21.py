import json

# 1. 95001 File 1: 不完全レコード (label=None) を除去
p1 = 'output/birukan/questions_json/95001/21_explanationText_added/question_95001_1_merged_explanationText_added.json'
with open(p1, 'r', encoding='utf-8') as f:
    d1 = json.load(f)

d1_clean = [q for q in d1 if q.get('questionLabel') is not None]
with open(p1, 'w', encoding='utf-8') as f:
    json.dump(d1_clean, f, ensure_ascii=False, indent=2)
print(f"95001 File 1 cleaned: {len(d1_clean)} records")

# 2. 95007 File 1: 問5 を追加
p7 = 'output/birukan/questions_json/95007/21_explanationText_added/question_95007_1_merged_explanationText_added.json'
with open(p7, 'r', encoding='utf-8') as f:
    d7 = json.load(f)

if not any(q.get('public_question_id') == '7e4b6deda712849b' for q in d7):
    q5 = {
        "questionBodyText": "次の建築物のうち、建築物衛生法に基づく特定建築物に該当しないものはどれか。\nただし、全て1棟の建築物とする。",
        "examLabel": "第54回（令和6年度（2024年）） 建築物衛生行政概論",
        "questionLabel": "問5",
        "choiceTextList": [
            "延べ面積が2,000m²の事務所と、2,500m²の社員研修所との複合施設",
            "延べ面積が8,500m²の中等教育学校",
            "延べ面積が5,000m²の予備校",
            "延べ面積が2,500m²の事務所と、1,000m²の事務所附属の倉庫との複合施設",
            "延べ面積が1,000m²の店舗と、1,500m²の事務所と事務所が管理する2,500m²の自然科学系研究施設との複合施設"
        ],
        "originalQuestionChoiceImageUrls": [[], [], [], [], []],
        "category": None,
        "examYear": 2024,
        "examOccurrenceId": "2024-r54",
        "list_group_id": "95007",
        "question_url": "https://birukan.kakomonn.com/questions/80785",
        "public_question_id": "7e4b6deda712849b",
        "original_question_id": "7e4b6deda712849b",
        "source_question_id": "birukan:kakomonn:questions:80785",
        "source_public_question_id": "51712d8bdd8df2ea",
        "questionSourceSite": "kakomonn",
        "canonical_question_key": "birukan:2024-r54:eisei-gyosei:q005",
        "question_id_policy_key": "canonical-question-key:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:{exam_occurrence_id_or_year}:q{question_number}",
        "sourceUniqueKeys": [
            "birukan:2024-r54:eisei-gyosei:q005:s01",
            "birukan:2024-r54:eisei-gyosei:q005:s02",
            "birukan:2024-r54:eisei-gyosei:q005:s03",
            "birukan:2024-r54:eisei-gyosei:q005:s04",
            "birukan:2024-r54:eisei-gyosei:q005:s05"
        ],
        "questionImageStorageUrls": [],
        "questionIntent": "select_incorrect",
        "correctChoiceText": [
            "正しい",
            "正しい",
            "正しい",
            "正しい",
            "間違い"
        ],
        "explanation_common_prefix": [],
        "explanation_common_prefix_inferred_correct_choice": 5,
        "explanation_common_summary": [],
        "explanation_choice_snippets": [],
        "explanation_choice_correctness": [],
        "answer_result_text": "正解は 5 です。",
        "answer_result_inferred_correct_choice_numbers": [5],
        "questionType": "true_false",
        "isCalculationQuestion": False,
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [],
        "lawContextForExplanation": "建築物における衛生的環境の確保に関する法律施行令第1条に基づく特定用途部分の延べ面積が判断軸。事務所・店舗・学校等は3,000m²以上、専ら学校教育法第1条に規定する学校は8,000m²以上。自然科学系研究施設は特定用途に含まれない。",
        "sourceQuestionKey": "birukan:95007:7e4b6deda712849b",
        "reviewQuestionId": "7e4b6deda712849b",
        "sourceRecordRef": "question_95007_1.json#4",
        "explanationReferences": [],
        "explanationText": [
            "正しい。事務所および研修所（学校教育法第1条以外の学校類似施設）はいずれも特定用途に該当します。合算した延べ面積が4,500m²（3,000m²以上）となるため特定建築物に該当します。",
            "正しい。学校教育法第1条に規定する中等教育学校は専ら学校の用に供される建築物に該当し、延べ面積が8,500m²（8,000m²以上）であるため特定建築物に該当します。",
            "正しい。専属の1条学校以外の予備校・専修学校等は3,000m²以上で特定建築物となります。延べ面積5,000m²であるため該当します。",
            "正しい。事務所に附属する専用倉庫は事務所の一部として合算算入されます。合算延べ面積が3,500m²（3,000m²以上）となるため特定建築物に該当します。",
            "間違い。正しくは、自然科学系研究施設は特定建築物の特定用途に該当せず面積合算の対象外です。特定用途である店舗（1,000m²）と事務所（1,500m²）の合計延べ面積は2,500m²にとどまり、基準である3,000m²未満となるため特定建築物には該当しません。"
        ],
        "lawRevisionFacts": [],
        "questionLearningPatternId": None,
        "suggestedQuestionDetailsByChoice": []
    }
    d7.append(q5)
    with open(p7, 'w', encoding='utf-8') as f:
        json.dump(d7, f, ensure_ascii=False, indent=2)
    print(f"95007 File 1 added q5: {len(d7)} records")

