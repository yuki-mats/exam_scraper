import json
import glob
import re
from pathlib import Path

def classify_question(q):
    exam = q.get('examLabel', '').split('）')[-1].strip()
    body = q.get('questionBodyText', '')
    choices = " ".join(q.get('choiceTextList', []))
    text = f"{body} {choices}"

    # 1. 建築物衛生行政概論
    if '行政概論' in exam:
        if any(k in text for k in ['歴史', '人物', '功績', 'WHO', '世界保健機関', '健康の定義', '保健所', '地域保健', '公衆衛生', '健康増進法', '受動喫煙']):
            return 'birukan_qs01_03_koshu_eisei'
        if any(k in text for k in ['登録', '指定団体', '立入検査', '改善命令', '監督', '管理技術者', '免状', '職務', '維持管理権原者', '行政組織', '国又は地方公共団体']):
            return 'birukan_qs01_02_gyosei_kanri'
        return 'birukan_qs01_01_hourei_seido'

    # 2. 建築物の構造概論
    if '構造概論' in exam:
        if any(k in text for k in ['設備', 'エレベーター', 'エスカレーター', '搬送', '電気', '受変電', '配電', '照明設備', '配線', '動力', '変圧器', '蓄電池']):
            return 'birukan_qs02_03_kenchiku_setsubi'
        if any(k in text for k in ['日照', '日射', '採光', '昼光', '音', '音響', '遮音', '騒音', '吸音', '色彩', '熱伝導', '熱貫流', '湿気', '結露', '気候']):
            return 'birukan_qs02_02_kenchiku_kankyo'
        return 'birukan_qs02_01_kozo_zairyo'

    # 3. 建築物の環境衛生
    if '環境衛生' in exam:
        if any(k in text for k in ['環境基本法', '環境基準', '作業区分', '作業環境測定', '事務所衛生基準規則', '管理手帳', '定期点検', '評価']):
            return 'birukan_qs03_02_eisei_kankyo'
        return 'birukan_qs03_01_kenko_kankyo'

    # 4. 空気環境の調整
    if '空気環境' in exam:
        if any(k in text for k in ['測定', '測定器', '検知管', '光散乱', 'インピディメンス', '非分散型赤外線', 'NDIR', '定電位電解', '基準値', '測定点', '中央管理方式']):
            return 'birukan_qs04_02_kuki_sokutei'
        if any(k in text for k in ['維持管理', '清掃', '点検', 'フィルタ', '集じん', '冷却塔', 'レジオネラ', 'ダクト', '自動制御', '加湿装置', '空気調和設備']):
            if any(k in text for k in ['冷凍機', 'ヒートポンプ', 'ボイラ', '空気線図', '熱負荷', '換気量', '必要換気', '風量', '送風機', '全熱交換器']):
                return 'birukan_qs04_01_kanki_kucho'
            return 'birukan_qs04_03_kuki_kanri'
        return 'birukan_qs04_01_kanki_kucho'

    # 5. 給水及び排水の管理
    if '給水及び排水' in exam:
        if any(k in text for k in ['水質', '残留塩素', '消毒', '次亜塩素酸', '検査', '水質基準', '硬度', '濁度', '色度', '味', '臭気', '大腸菌', '貯水槽清掃', '有効容量', '給水栓']):
            if any(k in text for k in ['給湯', '高置水槽', '直結', '受水槽', '配管']):
                if any(k in text for k in ['遊離残留塩素', '基準値', '水質基準項目']):
                    return 'birukan_qs05_03_suishitsu_kanri'
                return 'birukan_qs05_01_kyusui_kyuto'
            return 'birukan_qs05_03_suishitsu_kanri'
        if any(k in text for k in ['排水', '通気', 'トラップ', '阻集器', '汚水', '雑排水', '雨水', '破封', 'ルーフドレン', 'ディスポーザ', '浄化槽', '汚泥']):
            return 'birukan_qs05_02_haisui_tsuuki'
        return 'birukan_qs05_01_kyusui_kyuto'

    # 6. 清掃
    if '清掃' in exam:
        if any(k in text for k in ['廃棄物', 'ごみ', '3R', 'リサイクル', '分別', 'マニフェスト', '産業廃棄物', '一般廃棄物', '特別管理', '保管場所', '集積', '減量']):
            return 'birukan_qs06_02_haikibutsu_seiso_kanri'
        return 'birukan_qs06_01_seiso_gijutsu'

    # 7. ねずみ、昆虫等の防除
    if '防除' in exam:
        if any(k in text for k in ['薬剤', '殺虫剤', '殺鼠剤', '剤型', '毒性', '有機リン', 'ピレスロイド', 'カーバメート', 'IGR', '羽化阻害', '乳剤', '粉剤', '油剤', 'くん煙']):
            return 'birukan_qs07_03_bozai_yakuza'
        if any(k in text for k in ['調査', 'トラップ', '生息', '粘着', '防除', 'IPM', '防鼠', '防虫', 'ライトトラップ', '目視', '指数', '物理的']):
            return 'birukan_qs07_02_bojo_chosa'
        return 'birukan_qs07_01_nezumi_gaichu'

    # fallback by keyword
    if 'ねずみ' in text or '害虫' in text or 'ゴキブリ' in text:
        return 'birukan_qs07_01_nezumi_gaichu'
    if '清掃' in text or '床' in text or '洗剤' in text:
        return 'birukan_qs06_01_seiso_gijutsu'
    if '給水' in text or '給湯' in text or '配管' in text:
        return 'birukan_qs05_01_kyusui_kyuto'
    if '空気' in text or '換気' in text or '空調' in text:
        return 'birukan_qs04_01_kanki_kucho'
    if '建築' in text or '構造' in text:
        return 'birukan_qs02_01_kozo_zairyo'

    return 'birukan_qs01_01_hourei_seido'

# test classify on all questions
total_counts = {}
for lg in ['95001', '95002', '95003', '95004', '95005', '95006', '95007', '95008']:
    files = sorted(glob.glob(f'output/birukan/questions_json/{lg}/21_explanationText_added/*.json'))
    for f in files:
        with open(f) as fp:
            data = json.load(fp)
        for q in data:
            qs = classify_question(q)
            total_counts[qs] = total_counts.get(qs, 0) + 1

print("=== Classification Distribution across all 1435 questions ===")
for qs, cnt in sorted(total_counts.items()):
    print(f"{qs}: {cnt} questions")
