# -*- coding: utf-8 -*-
"""
Generate 4-layer patches for 2005 question_2005_1.json (Q1 - Q25)
"""
import json
from pathlib import Path

BASE_DIR = Path("/Users/yuki/development/exam_scraper")
SOURCE_FILE = BASE_DIR / "output/shinkyu/questions_json/2005/00_source/question_2005_1.json"

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
        "qid": "c228ada472240221",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。限られた医療資源の適正配分や医療保険制度のあり方（医療財政）は生命倫理の重要な検討対象です。",
            "間違い。損害賠償は民事法上の法的責任・損害補償の問題であり、生命の倫理的価値や規範を扱うバイオエシックスの直接の対象ではありません。",
            "正しい。人工授精や体外受精などの生殖補助医療は生命の誕生に関わる生命倫理の主要課題です。",
            "正しい。安楽死や尊厳死は終末期医療における自己決定権や生命の尊厳に関わる生命倫理の主要課題です。"
        ]
    },
    {
        "qid": "b1c544e0a13f5741",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。ヒポクラテスの誓いは古代ギリシャにおける医師の医の倫理・守秘義務の規範です。",
            "間違い。アルマ・アタ宣言（1978年）はプライマリヘルスケアの推進を提唱した宣言です。",
            "間違い。ジュネーブ宣言（1948年）は世界医師会による現代版ヒポクラテスの誓い（医師の倫理的義務）です。",
            "正しい。ヘルシンキ宣言（1964年）はヒトを対象とする医学研究の倫理原則を定めた国際規定であり、インフォームド・コンセント（十分な説明に基づく同意）の原則を明記しました。"
        ]
    },
    {
        "qid": "076d2bc898c1d69e",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。プライマリヘルスケア（PHC）では住民が利用しやすい「身近な医療（近接性）」が基本要素となります。",
            "正しい。患者の身体だけでなく心理・社会面も含めて診る「全人的把握（包括性）」はPHCの重要要素です。",
            "正しい。予防から治療・リハビリまで一貫して関わる「継続的な管理（継続性）」はPHCの重要要素です。",
            "間違い。特定領域の高度・先端医療である「専門的な医療（三次医療）」はプライマリヘルスケアの基本要素には含まれません。"
        ]
    },
    {
        "qid": "8b74db28968f8b16",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。リハビリテーションは発症後の機能回復や社会復帰、再発・悪化防止を図る「第三次予防」に該当します。",
            "間違い。禁煙は生活習慣の改善による疾病発症予防であり「第一次予防」に該当します。",
            "間違い。がん検診は無症状期に早期発見・早期治療を行う「第二次予防」に該当します。",
            "間違い。予防接種は病原体に対する特異的免疫を獲得して発症を防ぐ「第一次予防」に該当します。"
        ]
    },
    {
        "qid": "f1aef7d949c5b1ac",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。皮膚癌は紫外線曝露や肌のメラニン色素の差などにより欧米白人に極めて高頻度です。",
            "正しい。肝癌はB型・C型肝炎ウイルスの感染率が高かった歴史的背景などから、欧米に比べて我が国で罹患率・死亡率が高くなっています。",
            "間違い。大腸癌は食生活の欧米化により日本でも増加していますが、伝統的に欧米で高い罹患率を示します。",
            "間違い。乳癌は欧米女性に多くみられる代表的ながんです。"
        ]
    },
    {
        "qid": "46ea2a4b11a6998a",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "正しい", "間違い"],
        "explanations": [
            "正しい。トンネル掘削や鉱山作業などにおける鉱物性粉じんの吸入はじん肺の原因となります。",
            "正しい。騒音環境下での長期間作業は内耳有毛細胞を障害し騒音性難聴の原因となります。",
            "正しい。パソコン画面等を注視するVDT作業は眼精疲労や筋骨格系障害（VDT症候群）の原因となります。",
            "間違い。潜函病（減圧症）は潜函作業や潜水作業などの高気圧環境から急激に常圧へ減圧する際に、血液中に窒素の気泡が生じることで発症します。溶接作業による障害ではありません。"
        ]
    },
    {
        "qid": "657a6e690fb7a7f7",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。狂犬病の病原体は狂犬病ウイルス（ラブドウイルス科）です。リケッチアではありません。",
            "正しい。コレラの病原体はコレラ菌（*Vibrio cholerae*、グラム陰性桿菌・細菌）です。",
            "間違い。百日咳の病原体は百日咳菌（*Bordetella pertussis*、細菌）です。ウイルスではありません。",
            "間違い。日本脳炎の病原体は日本脳炎ウイルス（フラビウイルス科）です。細菌ではありません。"
        ]
    },
    {
        "qid": "b910f80dbf55b53d",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。感染症法施行当時の分類においてコレラは2類感染症に指定されていました（現行法では3類感染症）。",
            "正しい。感染症法施行当時の分類において腸チフスは2類感染症に指定されていました（現行法では3類感染症）。",
            "間違い。アメーバ赤痢は感染症法において「5類感染症（全数把握疾患）」に分類されており、2類感染症には含まれません。",
            "正しい。急性灰白髄炎（ポリオ）は感染症法において2類感染症に分類されています。"
        ]
    },
    {
        "qid": "de9debf2a7ce6541",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。消毒用エタノールは手指や注射部位などの生体皮膚消毒に汎用されます。",
            "正しい。逆性石けん（塩化ベンザルコニウム等）は手指や創傷皮膚の消毒に用いられます。",
            "間違い。ホルマリン（ホルムアルデヒド液）は強い組織毒性・刺激性・発がん性があるため、病理標本固定や器具消毒に限られ、人体皮膚の消毒には絶対に使用してはなりません。",
            "正しい。ヨードチンキやポビドンヨードは皮膚・術野の消毒に広く用いられます。"
        ]
    },
    {
        "qid": "a28a9900f35354af",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。生後1週（満7日）未満の死亡は「早期新生児死亡」と定義されます。",
            "間違い。生後4週（満28日）未満の死亡は「新生児死亡」と定義されます。",
            "正しい。人口動態統計において、乳児死亡は「生後1年未満の死亡」と定義されます。",
            "間違い。生後5年未満（1〜4歳）の死亡は幼児死亡などの区分となります。"
        ]
    },
    {
        "qid": "0c0e82a6a632e24e",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。あん摩マッサージ指圧師、はり師、きゅう師等に関する法律施行規則第6条において、免許を取り消されたときは「5日以内」に免許証を厚生労働大臣に返納しなければならないと規定されています。",
            "間違い。7日以内ではありません。",
            "間違い。10日以内ではありません。",
            "間違い。30日以内は再交付を受けた後亡失した免許証を発見した場合などの返納期限です。"
        ]
    },
    {
        "qid": "28965224cff90b84",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。施術者は正当な理由がなく、業務上知り得た人の秘密を漏らしてはなりません。",
            "間違い。あはき師法第13条の4に規定される秘密漏示罪は「親告罪」であり、被害者（告訴権者）の告訴がなければ公訴を提起（起訴）することはできません。",
            "正しい。法令に基づく証言や患者本人の同意など、正当な理由がある場合は例外となります。",
            "正しい。施術者でなくなった後（廃業・退職後）であっても、生涯にわたり守秘義務が課せられます。"
        ]
    },
    {
        "qid": "0b70bd96ecb35e1a",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。臨検検査の職権を持つのは「都道府県知事（保健所設置市長・特別区長）」です。市町村長ではありません。",
            "間違い。施術所の構造設備や衛生管理状況は臨検検査の直接の検査対象に含まれます。",
            "正しい。あはき師法第10条第2項において、臨検検査を行う当該職員はその身分を示す証票を携帯し、関係者に提示しなければならないと規定されています。",
            "間違い。臨検検査は行政上の取締り・指導を目的とするものであり、犯罪捜査のために認められたものと解釈してはなりません。"
        ]
    },
    {
        "qid": "5c5389d35392c440",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。診療所は療養病床（長期療養を必要とする患者のための病床）を開設することができます。",
            "正しい。病院や診療所は、医療法に基づく広告可能事項として予約診療の旨を広告することができます。",
            "間違い。助産所は助産師が開設する施設であり、嘱託医師・嘱託医療機関を定めればよく、常勤医師の配置義務はありません。",
            "正しい。特定機能病院は高度な医療の提供・技術開発・研修を行うため、原則として500床以上の病床数を有しなければなりません。"
        ]
    },
    {
        "qid": "7152f8063087ba16",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "間違い", "正しい"],
        "explanations": [
            "間違い。単層扁平上皮は血管内皮や肺胞などに存在し、物質交換に適しますが伸縮性はありません。",
            "間違い。重層扁平上皮は皮膚表皮や食道などに存在し、機械的摩擦に対する保護に適します。",
            "間違い。単層円柱上皮は胃腸粘膜などに存在し、吸収や分泌に適します。",
            "正しい。移行上皮（尿路上皮）は膀胱や尿管などに存在し、内腔の拡張・収縮に応じて細胞層が伸び縮みする最も伸縮性の高い上皮です。"
        ]
    },
    {
        "qid": "7edb12ae82b76301",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。上衣細胞は脳室や脊髄中心管の内面を覆い、脳脊髄液の循環に関与します。",
            "間違い。希突起膠細胞（オリゴデンドロサイト）は中枢神経系で髄鞘を形成します。",
            "正しい。星状膠細胞（アストロサイト）は突起の終末（血管周囲足）で脳毛細血管を取り囲み、血液脳関門（BBB）の形成と維持に重要な役割を果たします。",
            "間違い。小膠細胞（ミクログリア）は中枢神経系における貪食細胞（マクロファージ系）として機能します。"
        ]
    },
    {
        "qid": "e0c69b0582218db1",
        "intent": "select_correct",
        "choices_truth": ["間違い", "正しい", "間違い", "間違い"],
        "explanations": [
            "間違い。三角筋は上腕骨の「三角筋粗面」に停止します。",
            "正しい。棘下筋（および棘上筋・小円筋）は上腕骨の「大結節」に停止します。",
            "間違い。大円筋は上腕骨の「小結節稜」に停止します。",
            "間違い。肩甲下筋は上腕骨の「小結節」に停止します。"
        ]
    },
    {
        "qid": "5be47e2478e8a614",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。深指屈筋腱（4本）は手根管を通過します。",
            "間違い。長掌筋腱は屈筋支帯の浅層（表面）を通過して手掌腱膜に放散するため、手根管の内部は通過しません。",
            "正しい。長母指屈筋腱（1本）は手根管を通過します。",
            "正しい。浅指屈筋腱（4本）は手根管を通過します。"
        ]
    },
    {
        "qid": "60ce2e50cb0b98c2",
        "intent": "select_correct",
        "choices_truth": ["間違い", "間違い", "正しい", "間違い"],
        "explanations": [
            "間違い。梨状筋は仙骨神経叢（梨状筋神経）の支配を受けます。",
            "間違い。上双子筋は仙骨神経叢（内閉鎖筋神経）の支配を受けます。",
            "正しい。外閉鎖筋は腰神経叢の枝である「閉鎖神経（L2-L4）」の支配を受けます。",
            "間違い。大腿筋膜張筋は仙骨神経叢の枝である「上殿神経」の支配を受けます。"
        ]
    },
    {
        "qid": "bd4139bb0dbbd2b5",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。空腸および回腸は腸間膜によって後腹壁につながる腸間膜小腸です。",
            "間違い。半月ヒダは大腸（結腸）にみられる粘膜構造です。小腸の粘膜には「輪状ヒダ（ケルクリングヒダ）」が存在します。",
            "正しい。腸腺（リーベルキューン腺）は絨毛の根元（絨毛間窩）に開口します。",
            "正しい。小腸の筋層は内輪筋と外縦筋の二層の平滑筋から構成されます。"
        ]
    },
    {
        "qid": "d0ddf1380d10d49b",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。膵臓は腹膜後隙に位置する代表的な後腹膜器官です。",
            "間違い。膵尾は脾臓の脾門に達します。十二指腸に囲まれるのは膵頭です。",
            "間違い。膵臓は胃の後下面に位置し、肝臓の直下ではありません。",
            "間違い。膵管は総胆管と合流して十二指腸下行部の大十二指腸乳頭（ファーター乳頭）に開口します。幽門ではありません。"
        ]
    },
    {
        "qid": "e97e2ad468d3cbf8",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。喉頭蓋軟骨（および耳介軟骨・外耳道軟骨・耳管軟骨等）は弾性線維に富む「弾性軟骨」です。",
            "間違い。甲状軟骨は「硝子軟骨」です。",
            "間違い。輪状軟骨は「硝子軟骨」です。",
            "間違い。気管軟骨は「硝子軟骨」です。"
        ]
    },
    {
        "qid": "7f8f9f2dd9380742",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "間違い", "正しい", "正しい"],
        "explanations": [
            "正しい。前立腺は膀胱の直下に位置し、尿道を取り囲みます。",
            "間違い。前立腺は骨盤底の腹膜外（腹膜下器官）に位置し、腹膜には覆われていません。",
            "正しい。前立腺の導管（前立腺小管）は尿道前立腺部に直接開口します。",
            "正しい。前立腺の実質は腺組織とともに豊富な平滑筋（筋線維）を含み、射精時に収縮します。"
        ]
    },
    {
        "qid": "e3ddf87ccdd819ec",
        "intent": "select_correct",
        "choices_truth": ["正しい", "間違い", "間違い", "間違い"],
        "explanations": [
            "正しい。子宮は骨盤腔の中央にあり、前方の膀胱と後方の直腸の間に位置します。",
            "間違い。膣につながるのは子宮下部の「子宮頸部（外子宮口）」です。子宮底は上部のドーム状部分です。",
            "間違い。卵管につながるのは子宮上両側の「子宮角（体部）」です。子宮頸部は膣につながります。",
            "間違い。子宮筋層は厚い「平滑筋」から構成されており、横紋筋ではありません。"
        ]
    },
    {
        "qid": "328e36ba07c6526e",
        "intent": "select_incorrect",
        "choices_truth": ["正しい", "正しい", "間違い", "正しい"],
        "explanations": [
            "正しい。右腎は直上に肝臓が存在するため、左腎よりも約半椎体から1椎体低位にあります。",
            "正しい。ネフロン（腎単位）は腎小体（糸球体とボーマン嚢）とそれに続く尿細管から構成されます。",
            "間違い。ボーマン嚢の尿極から直接始まるのは「近位尿細管」です。遠位尿細管はヘンレのループ（太い上行脚）に続いて始まります。",
            "正しい。腎臓は後腹壁の腹膜後隙に位置する後腹膜器官です。"
        ]
    }
]

# Write patches
p10_list = []
p15_list = []
p23_list = []
p21_list = []
source_rel_path = "output/shinkyu/questions_json/2005/00_source/question_2005_1.json"

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

p10_file = P10_DIR / "question_2005_1_questionType_fixed.json"
p15_file = P15_DIR / "question_2005_1_correctChoiceText_fixed.json"
p23_file = P23_DIR / "question_2005_1_correctChoiceText_fixed.json"
p21_file = P21_DIR / "question_2005_1_explanationText_added.json"

with open(p10_file, "w", encoding="utf-8") as f:
    json.dump(p10_list, f, ensure_ascii=False, indent=2)

with open(p15_file, "w", encoding="utf-8") as f:
    json.dump(p15_list, f, ensure_ascii=False, indent=2)

with open(p23_file, "w", encoding="utf-8") as f:
    json.dump(p23_list, f, ensure_ascii=False, indent=2)

with open(p21_file, "w", encoding="utf-8") as f:
    json.dump(p21_list, f, ensure_ascii=False, indent=2)

print("Generated patches for question_2005_1 successfully.")
