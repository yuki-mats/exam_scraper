#!/usr/bin/env python3
"""Build stable tsukanshi 01-04 artifacts for Firestore preparation.

This script intentionally writes deterministic patch filenames without a
timestamp so reruns overwrite the same artifacts instead of creating another
generation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.fix.auto_assign_correct_choice_text import build_expected_correct_choice_text


DEFAULT_BASE_DIR = ROOT_DIR / "output" / "tsukanshi" / "questions_json"
DEFAULT_CATEGORY_JSON = ROOT_DIR / "output" / "tsukanshi" / "category" / "category.json"

OFFICIAL_SUBJECT_FOLDERS = {
    "通関業法": ["tsukanshi_f01_tsukangyoho"],
    "関税法、関税定率法その他関税に関する法律及び外国為替及び外国貿易法": [
        "tsukanshi_f02_kanzeiho_tsukan",
        "tsukanshi_f03_kanzeiho_hozei",
        "tsukanshi_f04_kanzeiho_nozei",
        "tsukanshi_f05_kanzeiteiritsuho",
        "tsukanshi_f06_kanzeikankeiho_gaikokukawaseho",
    ],
    "通関書類の作成要領その他通関手続の実務": ["tsukanshi_f07_tsukanjitsumu"],
}

QUESTION_SET_ALIASES = {
    "tsukanshi_qs01_01_yogo_tsukangyomu": [
        "通関業法第1条",
        "通関業法第2条",
        "同法の目的",
        "用語の定義",
        "関連業務",
        "通関業務",
        "目的",
    ],
    "tsukanshi_qs01_02_tsukangyo_kyoka_shinsei": ["通関業の許可", "許可申請"],
    "tsukanshi_qs01_03_kyoka_kijun_kekkaku": ["欠格事由", "許可基準", "資産", "信用"],
    "tsukanshi_qs01_04_kyoka_shometsu_torikeshi_shokei": [
        "許可の消滅",
        "許可の取消し",
        "地位の承継",
        "許可の承継",
        "承継",
        "廃業",
        "合併",
        "解散",
        "死亡",
    ],
    "tsukanshi_qs01_05_eigyousho_henko_todokede": [
        "営業所",
        "変更等の届出",
        "変更の届出",
        "営業区域の制限",
        "新設",
    ],
    "tsukanshi_qs01_06_tsukangyosha_gimu_shinsa": [
        "通関業者の義務",
        "通関業者及び通関士の義務",
        "通関業者又は通関士の義務",
        "通関業法に基づく義務",
        "記帳",
        "届出",
        "報告",
        "通関書類の審査",
        "記名",
        "料金",
        "秘密保持",
        "名義貸し",
    ],
    "tsukanshi_qs01_07_tsukangyosha_kenri": [
        "意見の聴取",
        "検査の通知",
        "更正に関する意見の聴取",
        "通関業者の権利",
    ],
    "tsukanshi_qs01_08_tsukanshi_setchi_kakunin_shinsa": [
        "通関士の設置",
        "通関士の審査",
        "財務大臣の確認",
        "税関長の確認",
        "通関士となる資格",
        "通関士の資格",
    ],
    "tsukanshi_qs01_09_tsukanshi_gimu_shikaku": [
        "通関士の義務",
        "資格の喪失",
        "信用失墜",
        "合格の取消し",
    ],
    "tsukanshi_qs01_10_kantoku_chokai_gyomu_kaizen": [
        "業務改善命令",
        "監督処分",
        "懲戒処分",
        "戒告",
        "業務停止",
    ],
    "tsukanshi_qs01_11_tetsuzuki_hokoku_fufuku": [
        "処分の手続",
        "調査の申出",
        "審査委員",
        "公告",
        "報告徴取",
        "不服申立て",
        "権限の委任",
    ],
    "tsukanshi_qs01_12_bassoku_meisho_seigen": ["罰則", "名称使用制限", "両罰規定", "過料"],
    "tsukanshi_qs02_01_yogo_teigi": [
        "用語の定義",
        "関税法における用語の定義",
        "関税法及び関税定率法における用語の定義",
        "輸入",
        "輸出",
        "外国貨物",
        "内国貨物",
        "本邦",
    ],
    "tsukanshi_qs02_02_tsukanzente_tetsuzuki": ["船舶", "航空機", "入港", "出港", "積卸し", "通関前"],
    "tsukanshi_qs02_03_yushutsu_tsukan": ["輸出通関", "輸出申告", "輸出許可"],
    "tsukanshi_qs02_04_tokutei_yushutsu_shinkoku": [
        "特定輸出申告",
        "特定輸出者",
        "特定委託輸出者",
        "特定製造貨物輸出者",
        "認定製造者",
    ],
    "tsukanshi_qs02_05_yushutsu_kisei_tsumimodoshi": [
        "他法令確認",
        "他の法令の規定に関する証明又は確認",
        "輸出してはならない貨物",
        "積戻し",
        "輸出規制",
    ],
    "tsukanshi_qs02_06_yunyu_tsukan": ["輸入通関", "輸入申告", "輸入許可"],
    "tsukanshi_qs02_07_tokurei_yunyu_hikitori": [
        "特例輸入者及び特定輸出者",
        "特例輸入",
        "特例申告",
        "特例輸入者",
        "許可前引取り",
        "許可前における貨物の引取り",
        "保全担保",
    ],
    "tsukanshi_qs02_08_yunyu_kisei_gensanchi": [
        "輸入してはならない貨物",
        "原産地を偽った表示",
        "原産地表示",
        "原産地虚偽表示",
        "原産地証明書",
        "輸入規制",
    ],
    "tsukanshi_qs02_09_yubinbutsu": ["郵便物", "国際郵便"],
    "tsukanshi_qs02_10_nintei_tsukangyosha": ["認定通関業者", "AEO", "開庁時間外", "事務執行の求め"],
    "tsukanshi_qs03_01_hozei_soron_hozou": ["保税地域", "指定保税地域", "保税蔵置場", "蔵置"],
    "tsukanshi_qs03_02_hozei_kojo_tenjijo_sogo": [
        "保税工場",
        "保税展示場",
        "総合保税地域",
        "保税作業",
        "移入承認",
    ],
    "tsukanshi_qs03_03_hozei_unsou_tokutei": [
        "保税運送",
        "特定保税運送",
        "運送承認",
        "運送期間",
        "貨物の運送",
    ],
    "tsukanshi_qs03_04_shuyo_ryuchi": ["収容", "留置", "公売"],
    "tsukanshi_qs04_01_kazeibukken_tekiyo_horei": [
        "課税物件",
        "課税物件の確定の時期",
        "適用法令",
        "輸入の時",
        "法令適用",
    ],
    "tsukanshi_qs04_02_nozei_gimusha_kakutei_hoshiki": [
        "納税義務",
        "納税義務者",
        "補完的納税義務",
        "税額の確定方式",
        "関税の確定",
    ],
    "tsukanshi_qs04_03_shinkoku_fuka_kazei": ["申告納税方式", "賦課課税方式", "納税告知", "納税申告"],
    "tsukanshi_qs04_04_teisei_kosei_kettei": ["修正申告", "更正の請求", "更正及び決定", "更正", "決定"],
    "tsukanshi_qs04_05_nofu_nokigen": ["関税の納付", "納期限", "法定納期限", "納付手続"],
    "tsukanshi_qs04_06_tanpo_choshu": ["担保", "徴収", "徴収の順位", "徴収の引継ぎ"],
    "tsukanshi_qs04_07_futaizei": [
        "附帯税",
        "延滞税",
        "過少申告加算税",
        "無申告加算税",
        "重加算税",
        "加算税",
    ],
    "tsukanshi_qs04_08_kanpu_juto_jiko": ["還付", "過誤納金", "期間制限", "消滅時効", "除斥期間"],
    "tsukanshi_qs04_09_fufuku_moshitate": ["不服申立て", "審査請求", "再調査の請求", "訴訟"],
    "tsukanshi_qs04_10_zassoku_choho_kanrinin": [
        "税関事務管理人",
        "事後調査",
        "帳簿書類",
        "帳簿",
        "非居住者",
        "税関職員の権限",
    ],
    "tsukanshi_qs04_11_bassoku_hansoku": ["犯則事件", "通告処分", "没収", "追徴", "関税法違反", "罰則"],
    "tsukanshi_qs05_01_kanzeiritsu_kaniritsu": ["関税率", "簡易税率", "少額輸入貨物", "携帯品", "適用税率"],
    "tsukanshi_qs05_02_tokushu_kanzei": ["特殊関税", "報復関税", "相殺関税", "不当廉売関税", "緊急関税", "対抗関税"],
    "tsukanshi_qs05_03_kazei_kakaku_gensoku": ["課税価格の決定の原則", "現実支払価格", "加算要素", "控除要素", "輸入取引"],
    "tsukanshi_qs05_04_kazei_kakaku_reigai_tokurei": ["同種又は類似の貨物", "国内販売価格", "製造原価", "航空運賃特例", "例外的決定方法", "為替相場"],
    "tsukanshi_qs05_05_genmen_modoshi_soron": ["減免税", "軽減", "免除", "払戻し", "用途外使用", "転用"],
    "tsukanshi_qs05_06_henshitsu_sonsho_saiyunyu": ["変質", "損傷", "加工又は修繕", "加工修繕", "再輸入"],
    "tsukanshi_qs05_07_mujoken_tokutei_yoto_menzei": ["無条件免税", "特定用途免税", "外交官用貨物", "水産物"],
    "tsukanshi_qs05_08_saiyushutsu_menzei_modoshi": ["再輸出免税", "再輸出減税", "違約品", "廃棄", "同一状態", "戻し税"],
    "tsukanshi_qs05_09_genryohin_genmen_modoshi": ["製造用原料品", "輸出貨物製造用原料品", "内貨原料品", "課税原料品"],
    "tsukanshi_qs05_10_kanzeiritsuhyo_hinmoku_bunrui": ["関税率表", "解釈通則", "品目分類", "HS", "所属の決定", "税番", "類注", "部注"],
    "tsukanshi_qs06_01_kanzei_zantei_sochiho": ["関税暫定措置法", "航空機部分品", "特別緊急関税", "軽減税率"],
    "tsukanshi_qs06_02_tokkei_kanzei": ["特恵関税", "特恵受益国", "一般特恵"],
    "tsukanshi_qs06_03_epa_seido": [
        "経済連携協定",
        "EPA",
        "譲許",
        "EPA税率",
        "関税割当",
        "TPP11",
        "RCEP",
        "オーストラリア協定",
        "タイ協定",
        "モンゴル協定",
        "スイス協定",
    ],
    "tsukanshi_qs06_04_naccs_ho": ["NACCS法", "電子情報処理組織", "輸出入等関連業務"],
    "tsukanshi_qs06_05_container_ata_tokureiho": ["コンテナー特例法", "ATA", "ATAカルネ", "TIR", "通関手帳"],
    "tsukanshi_qs06_06_churyu_beigun_tokureiho": ["駐留米軍", "地位協定", "臨時特例法", "米軍"],
    "tsukanshi_qs06_07_gaitame_yushutsu_boueki_kanrirei": ["外国為替及び外国貿易法第48条", "輸出貿易管理令", "輸出の許可及び承認", "輸出許可及び承認"],
    "tsukanshi_qs06_08_gaitame_yunyu_boueki_kanrirei": ["外国為替及び外国貿易法第52条", "輸入貿易管理令", "輸入の承認", "輸入割当"],
    "tsukanshi_qs07_01_yushutsu_shinkokusho": ["輸出申告書", "輸出申告事項登録画面", "輸出通関"],
    "tsukanshi_qs07_02_yunyu_nozei_shinkokusho": ["輸入申告書", "輸入納税申告書", "輸入申告事項登録画面", "NACCS"],
    "tsukanshi_qs07_03_tsukan_tetsuzuki_jitsumu": ["通関手続の実務", "書類", "実務判断", "総合問題", "貨物を輸入しようとする者"],
    "tsukanshi_qs07_04_tekiyo_zeiritsu_kanzeigaku_keisan": ["適用税率", "関税額", "消費税額", "地方消費税額", "納付すべき関税"],
    "tsukanshi_qs07_05_shusei_kosei_keisan": ["修正申告", "更正の請求", "過納金額", "減額する関税額", "追加納付"],
    "tsukanshi_qs07_06_futaizei_keisan": ["延滞税額", "過少申告加算税額", "無申告加算税額", "重加算税額", "附帯税"],
    "tsukanshi_qs07_07_kazei_kakaku_keisan": ["課税価格を計算", "課税価格の計算", "現実支払価格", "加算要素", "控除要素", "同種又は類似の貨物"],
    "tsukanshi_qs07_08_hinmoku_bunrui_jitsumu": ["関税率表", "所属の決定", "品目分類", "事前照会", "税番", "類注", "部注", "解釈通則"],
    "tsukanshi_qs07_09_epa_gensanchi_hantei": ["経済連携協定", "原産地規則", "原産品", "原産材料", "非原産材料", "譲許の便益", "EPA"],
}

SUBJECT_RULES = {
    "通関業法": [
        (r"罰則|名称使用制限|両罰|過料", "tsukanshi_qs01_12_bassoku_meisho_seigen"),
        (r"処分の手続|調査の申出|審査委員|公告|報告徴取|不服申立て|権限の委任", "tsukanshi_qs01_11_tetsuzuki_hokoku_fufuku"),
        (r"業務改善命令|監督処分|懲戒処分|戒告|業務停止", "tsukanshi_qs01_10_kantoku_chokai_gyomu_kaizen"),
        (r"通関士の義務|資格の喪失|信用失墜|合格の取消し", "tsukanshi_qs01_09_tsukanshi_gimu_shikaku"),
        (r"通関士の設置|通関士の審査|第31条.*確認|財務大臣の確認|税関長の確認|通関士となる資格|通関士の資格(?!の喪失)", "tsukanshi_qs01_08_tsukanshi_setchi_kakunin_shinsa"),
        (r"意見の聴取|検査の通知|更正に関する意見|通関業者の権利", "tsukanshi_qs01_07_tsukangyosha_kenri"),
        (r"通関業者.*通関士.*義務|通関業者の義務|通関業法に基づく義務|通関書類の審査|記帳|記名|料金|秘密保持|名義貸し", "tsukanshi_qs01_06_tsukangyosha_gimu_shinsa"),
        (r"営業所|変更等の届出|変更の届出|営業区域の制限|営業所の新設|新設", "tsukanshi_qs01_05_eigyousho_henko_todokede"),
        (r"消滅|取消し|承継|廃業|合併|解散|死亡", "tsukanshi_qs01_04_kyoka_shometsu_torikeshi_shokei"),
        (r"欠格事由|許可基準", "tsukanshi_qs01_03_kyoka_kijun_kekkaku"),
        (r"通関業の許可|許可申請", "tsukanshi_qs01_02_tsukangyo_kyoka_shinsei"),
        (r"通関業務|関連業務|用語の定義|同法の目的|第1条|第2条", "tsukanshi_qs01_01_yogo_tsukangyomu"),
    ],
    "関税法、関税定率法その他関税に関する法律及び外国為替及び外国貿易法": [
        (r"外国為替及び外国貿易法第52条|輸入貿易管理令|輸入の承認|輸入割当", "tsukanshi_qs06_08_gaitame_yunyu_boueki_kanrirei"),
        (r"外国為替及び外国貿易法第48条|輸出貿易管理令|輸出の許可及び承認|輸出許可及び承認", "tsukanshi_qs06_07_gaitame_yushutsu_boueki_kanrirei"),
        (r"コンテナー特例法|ATA|ATAカルネ|TIR|通関手帳", "tsukanshi_qs06_05_container_ata_tokureiho"),
        (r"駐留米軍|地位協定|臨時特例法|米軍", "tsukanshi_qs06_06_churyu_beigun_tokureiho"),
        (r"NACCS法|電子情報処理組織による輸出入等関連業務", "tsukanshi_qs06_04_naccs_ho"),
        (r"特恵関税制度|特恵関税|特恵受益国", "tsukanshi_qs06_02_tokkei_kanzei"),
        (r"経済連携協定|EPA|譲許|TPP11|RCEP|オーストラリア協定|タイ協定|モンゴル協定|スイス協定", "tsukanshi_qs06_03_epa_seido"),
        (r"関税暫定措置法", "tsukanshi_qs06_01_kanzei_zantei_sochiho"),
        (r"関税率表の解釈|関税率表|解釈通則|品目分類|HS|所属の決定|税番|類注|部注", "tsukanshi_qs05_10_kanzeiritsuhyo_hinmoku_bunrui"),
        (r"相殺関税|不当廉売関税|緊急関税|特殊関税|報復関税|対抗関税", "tsukanshi_qs05_02_tokushu_kanzei"),
        (r"同種又は類似の貨物|国内販売価格|製造原価|航空運賃特例|例外的決定方法|為替相場", "tsukanshi_qs05_04_kazei_kakaku_reigai_tokurei"),
        (r"課税価格の決定の原則|現実支払価格|加算要素|控除要素|輸入取引", "tsukanshi_qs05_03_kazei_kakaku_gensoku"),
        (r"製造用原料品|輸出貨物製造用原料品|内貨原料品|課税原料品", "tsukanshi_qs05_09_genryohin_genmen_modoshi"),
        (r"再輸出免税|再輸出減税|違約品|廃棄|同一状態|戻し税", "tsukanshi_qs05_08_saiyushutsu_menzei_modoshi"),
        (r"無条件免税|特定用途免税|外交官用貨物|水産物", "tsukanshi_qs05_07_mujoken_tokutei_yoto_menzei"),
        (r"変質|損傷|加工又は修繕|加工修繕|再輸入", "tsukanshi_qs05_06_henshitsu_sonsho_saiyunyu"),
        (r"軽減、免除又は払戻し|軽減又は免除|減免税|免除|払戻し|用途外使用|転用", "tsukanshi_qs05_05_genmen_modoshi_soron"),
        (r"関税率|簡易税率|少額輸入貨物|携帯品|適用税率", "tsukanshi_qs05_01_kanzeiritsu_kaniritsu"),
        (r"犯則事件|通告処分|没収|追徴|関税法第10章|罰則", "tsukanshi_qs04_11_bassoku_hansoku"),
        (r"税関事務管理人|事後調査|帳簿書類|帳簿|非居住者|税関職員の権限", "tsukanshi_qs04_10_zassoku_choho_kanrinin"),
        (r"不服申立て|審査請求|再調査の請求|訴訟", "tsukanshi_qs04_09_fufuku_moshitate"),
        (r"還付|過誤納金|期間制限|消滅時効|除斥期間", "tsukanshi_qs04_08_kanpu_juto_jiko"),
        (r"附帯税|延滞税|過少申告加算税|無申告加算税|重加算税|関税に係る加算税", "tsukanshi_qs04_07_futaizei"),
        (r"担保|徴収(?!.*郵便)|徴収の順位|徴収の引継ぎ", "tsukanshi_qs04_06_tanpo_choshu"),
        (r"納期限|法定納期限|関税の納付|納付及び徴収", "tsukanshi_qs04_05_nofu_nokigen"),
        (r"修正申告|更正の請求|更正及び決定|更正|決定", "tsukanshi_qs04_04_teisei_kosei_kettei"),
        (r"申告納税方式|賦課課税方式|納税告知|納税申告", "tsukanshi_qs04_03_shinkoku_fuka_kazei"),
        (r"納税義務|納税義務者|補完的納税義務|税額の確定方式|関税の確定", "tsukanshi_qs04_02_nozei_gimusha_kakutei_hoshiki"),
        (r"課税物件|課税物件の確定の時期|適用法令|輸入の時|法令適用", "tsukanshi_qs04_01_kazeibukken_tekiyo_horei"),
        (r"収容|留置|公売", "tsukanshi_qs03_04_shuyo_ryuchi"),
        (r"保税運送|特定保税運送|運送承認|運送期間|貨物の運送", "tsukanshi_qs03_03_hozei_unsou_tokutei"),
        (r"保税工場|保税展示場|総合保税地域|保税作業|移入承認", "tsukanshi_qs03_02_hozei_kojo_tenjijo_sogo"),
        (r"保税地域|指定保税地域|保税蔵置場|蔵置", "tsukanshi_qs03_01_hozei_soron_hozou"),
        (r"認定通関業者|AEO|開庁時間外|事務執行の求め", "tsukanshi_qs02_10_nintei_tsukangyosha"),
        (r"郵便物|国際郵便", "tsukanshi_qs02_09_yubinbutsu"),
        (r"輸入してはならない貨物|原産地を偽った表示|原産地表示|原産地虚偽表示|原産地証明書", "tsukanshi_qs02_08_yunyu_kisei_gensanchi"),
        (r"特例輸入者及び特定輸出者|特例輸入|特例申告|特例輸入者|許可前引取り|許可前における貨物の引取り|保全担保", "tsukanshi_qs02_07_tokurei_yunyu_hikitori"),
        (r"輸入通関|輸入申告|輸入許可", "tsukanshi_qs02_06_yunyu_tsukan"),
        (r"他法令確認|他の法令の規定に関する証明又は確認|輸出してはならない貨物|積戻し|輸出規制", "tsukanshi_qs02_05_yushutsu_kisei_tsumimodoshi"),
        (r"特定輸出申告|特定輸出者|特定委託輸出者|特定製造貨物輸出者|認定製造者", "tsukanshi_qs02_04_tokutei_yushutsu_shinkoku"),
        (r"輸出通関|輸出申告|輸出許可", "tsukanshi_qs02_03_yushutsu_tsukan"),
        (r"船舶|航空機|入港|出港|積卸し|通関前", "tsukanshi_qs02_02_tsukanzente_tetsuzuki"),
        (r"用語の定義|外国貨物|内国貨物|本邦", "tsukanshi_qs02_01_yogo_teigi"),
    ],
    "通関書類の作成要領その他通関手続の実務": [
        (r"原産地規則|原産品|原産材料|非原産材料|経済連携協定|譲許の便益|EPA", "tsukanshi_qs07_09_epa_gensanchi_hantei"),
        (r"関税率表|所属の決定|品目分類|事前照会|税番|類注|部注|解釈通則", "tsukanshi_qs07_08_hinmoku_bunrui_jitsumu"),
        (r"課税価格を計算|課税価格の計算|現実支払価格|加算要素|控除要素|同種又は類似の貨物", "tsukanshi_qs07_07_kazei_kakaku_keisan"),
        (r"延滞税額|過少申告加算税額|無申告加算税額|重加算税額|附帯税", "tsukanshi_qs07_06_futaizei_keisan"),
        (r"修正申告|更正の請求|過納金額|減額する関税額|追加納付", "tsukanshi_qs07_05_shusei_kosei_keisan"),
        (r"適用税率|関税額|消費税額|地方消費税額|納付すべき関税", "tsukanshi_qs07_04_tekiyo_zeiritsu_kanzeigaku_keisan"),
        (r"貨物を輸入しようとする者|実務問題|通関手続の実務|書類|実務判断|総合問題", "tsukanshi_qs07_03_tsukan_tetsuzuki_jitsumu"),
        (r"輸入申告書|輸入納税申告書|輸入申告事項登録画面|輸入通関", "tsukanshi_qs07_02_yunyu_nozei_shinkokusho"),
        (r"輸出申告書|輸出申告事項登録画面|輸出通関", "tsukanshi_qs07_01_yushutsu_shinkokusho"),
    ],
}

SUGGESTED_QUESTIONS = [
    "この問題の正答根拠は何か。",
    "誤りの選択肢はどこが違うか。",
    "同じ論点を再出題されたら何を確認するか。",
]
SUGGESTED_DETAILS = [
    {
        "question": SUGGESTED_QUESTIONS[0],
        "answer": "問題文、正答番号、解説素材を照合し、根拠となる条文、制度趣旨、計算関係を確認する。",
    },
    {
        "question": SUGGESTED_QUESTIONS[1],
        "answer": "主体、対象、期間、数値、手続、適用範囲のどれが正しい内容とずれているかを確認する。",
    },
    {
        "question": SUGGESTED_QUESTIONS[2],
        "answer": "同じ条文・制度・計算パターンで問われるキーワードと例外をセットで確認する。",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[DRY RUN] write: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(flatten_strings(item))
        return result
    return []


def extract_topic(question_body_text: str) -> str:
    body = normalize_text(question_body_text)
    patterns = [
        r"^次の記述は、(.+?)に関するものであるが",
        r"^次の記述は、(.+?)の規定に関するものであるが",
        r"^次の記述は、(.+?)について.*?(?:ものであるが|どれか|一つ選びなさい|選びなさい)",
        r"^次のAからEまでは(.+?)に関するもので",
    ]
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip(" 。")
    return ""


def build_term_index(question_sets: dict[str, dict[str, Any]]) -> tuple[dict[str, list[str]], Counter[str]]:
    term_index: dict[str, list[str]] = {}
    document_frequency: Counter[str] = Counter()
    for question_set_id, spec in question_sets.items():
        terms: list[str] = []
        seen_terms: set[str] = set()
        for raw_term in QUESTION_SET_ALIASES.get(question_set_id, []) + list(spec.get("matchingHints") or []):
            term = normalize_text(raw_term)
            if not term:
                continue
            terms.append(term)
            seen_terms.add(term)
        term_index[question_set_id] = terms
        for term in seen_terms:
            document_frequency[term] += 1
    return term_index, document_frequency


def build_context(category_json: Path) -> dict[str, Any]:
    category = load_json(category_json)
    folders = category.get("folders")
    question_sets = category.get("questionSets")
    if not isinstance(folders, list) or not isinstance(question_sets, list):
        raise ValueError(f"category.json の folders/questionSets が不正です: {category_json}")

    folder_specs: dict[str, dict[str, Any]] = {}
    question_set_specs: dict[str, dict[str, Any]] = {}
    question_set_ids_by_folder: dict[str, list[str]] = defaultdict(list)

    for folder in folders:
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("folderId") or "")
        if folder_id:
            folder_specs[folder_id] = dict(folder)

    for question_set in question_sets:
        if not isinstance(question_set, dict):
            continue
        question_set_id = str(question_set.get("questionSetId") or "")
        folder_id = str(question_set.get("folderId") or "")
        if not question_set_id or not folder_id:
            continue
        question_set_specs[question_set_id] = dict(question_set)
        question_set_ids_by_folder[folder_id].append(question_set_id)

    term_index, document_frequency = build_term_index(question_set_specs)
    return {
        "category": category,
        "folder_specs": folder_specs,
        "question_sets": question_set_specs,
        "question_set_ids_by_folder": dict(question_set_ids_by_folder),
        "term_index": term_index,
        "term_document_frequency": document_frequency,
    }


_ACTIVE_CONTEXT = build_context(DEFAULT_CATEGORY_JSON)
QUESTION_SETS: dict[str, dict[str, Any]] = _ACTIVE_CONTEXT["question_sets"]


def activate_context(category_json: Path) -> None:
    global _ACTIVE_CONTEXT
    global QUESTION_SETS
    _ACTIVE_CONTEXT = build_context(category_json)
    QUESTION_SETS = _ACTIVE_CONTEXT["question_sets"]


def source_files(base_dir: Path) -> list[Path]:
    return sorted(base_dir.glob("*/00_source/question_*.json"))


def source_questions(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    questions = payload.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"question_bodies not found: {path}")
    return [question for question in questions if isinstance(question, dict)]


def original_id(question: dict[str, Any]) -> str:
    value = question.get("original_question_id") or question.get("public_question_id")
    if not value:
        raise ValueError(f"original_question_id/public_question_id not found: {question.get('question_url')}")
    return str(value)


def exam_subject(question: dict[str, Any]) -> str:
    label = str(question.get("examLabel") or "")
    for subject in OFFICIAL_SUBJECT_FOLDERS:
        if subject in label:
            return subject
    raise ValueError(f"通関士 category 未定義の examLabel です: {label}")


def question_set_candidates(subject: str) -> list[str]:
    question_set_ids: list[str] = []
    for folder_id in OFFICIAL_SUBJECT_FOLDERS[subject]:
        question_set_ids.extend(_ACTIVE_CONTEXT["question_set_ids_by_folder"].get(folder_id, []))
    return question_set_ids


def classification_corpus(question: dict[str, Any]) -> tuple[str, str, str]:
    body = normalize_text(question.get("questionBodyText") or "")
    topic = extract_topic(body)
    full_text_parts = [
        body,
        *flatten_strings(question.get("explanation_common_prefix")),
        *flatten_strings(question.get("explanation_common_summary")),
        *flatten_strings(question.get("explanation_choice_snippets")),
        str(question.get("answer_result_text") or ""),
    ]
    full_text = normalize_text(" ".join(full_text_parts))
    return body, topic, full_text


def apply_subject_rules(subject: str, body: str, topic: str) -> str | None:
    for pattern, question_set_id in SUBJECT_RULES[subject]:
        if topic and re.search(pattern, topic):
            return question_set_id
    for pattern, question_set_id in SUBJECT_RULES[subject]:
        if re.search(pattern, body):
            return question_set_id
    return None


def score_term(term: str, *, topic: str, body: str, full_text: str) -> float:
    document_frequency = _ACTIVE_CONTEXT["term_document_frequency"].get(term, 1)
    weight = (len(term) + 2) / document_frequency
    score = 0.0
    if topic and term in topic:
        score += weight * 6
    if term in body:
        score += weight * 2
    if term in full_text:
        score += weight
    return score


def classify_question_set(question: dict[str, Any]) -> tuple[str, str]:
    subject = exam_subject(question)
    body, topic, full_text = classification_corpus(question)
    ruled = apply_subject_rules(subject, body, topic)
    if ruled:
        return ruled, "rule"

    candidates = question_set_candidates(subject)
    scores: dict[str, float] = {}
    for question_set_id in candidates:
        terms = _ACTIVE_CONTEXT["term_index"].get(question_set_id, [])
        score = 0.0
        for term in dict.fromkeys(terms):
            score += score_term(term, topic=topic, body=body, full_text=full_text)
        scores[question_set_id] = score

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][1] <= 0:
        raise ValueError(
            "questionSetId を判定できませんでした: "
            f"{question.get('question_url')} body={body[:160]}"
        )
    if len(ranked) >= 2 and ranked[0][1] == ranked[1][1]:
        raise ValueError(
            "questionSetId 判定が同点です: "
            f"{question.get('question_url')} top={ranked[:3]} topic={topic or body[:120]}"
        )
    return ranked[0][0], "score"


def question_subject(question: dict[str, Any]) -> str:
    """互換性のために残す。現在は科目ではなく questionSetId を返す。"""

    question_set_id, _ = classify_question_set(question)
    return question_set_id


def patch_path(source_path: Path, subdir: str, tag: str) -> Path:
    list_group_dir = source_path.parents[1]
    return list_group_dir / subdir / f"{source_path.stem}_{tag}.json"


def normalize_snippet_list(source_snippets: Any) -> list[list[str]]:
    if not isinstance(source_snippets, list):
        return []
    normalized: list[list[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list):
            normalized.append([text for text in entry if isinstance(text, str) and text.strip()])
        elif isinstance(entry, str) and entry.strip():
            normalized.append([entry])
        else:
            normalized.append([])
    return normalized


def normalize_first_snippet_list(source_snippets: Any) -> list[list[str]]:
    if not isinstance(source_snippets, list):
        return []
    normalized: list[list[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list) and entry:
            first = entry[0]
            normalized.append([first] if isinstance(first, str) and first.strip() else [])
        elif isinstance(entry, str) and entry.strip():
            normalized.append([entry])
        else:
            normalized.append([])
    return normalized


def resolved_correct_choice_text(question: dict[str, Any]) -> tuple[list[str], bool, str]:
    source_labels = question.get("correctChoiceText")
    source_list = source_labels if isinstance(source_labels, list) else []
    expected, reason = build_expected_correct_choice_text(question)
    if expected is None:
        detail = ""
        if reason:
            detail = f"source correctChoiceText を維持: {reason}"
        return source_list, False, detail

    if expected == source_list:
        return expected, False, ""

    answer_text = str(question.get("answer_result_text") or "").strip()
    intent = str(question.get("questionIntent") or "").strip()
    detail = f"answer_result_text={answer_text} questionIntent={intent}"
    return expected, True, detail


def compact_join(parts: list[str], *, max_chars: int = 1800) -> str:
    seen: set[str] = set()
    output: list[str] = []
    total = 0
    for raw in parts:
        text = " ".join(str(raw).split())
        if not text or text in seen:
            continue
        seen.add(text)
        if total + len(text) + 2 > max_chars:
            break
        output.append(text)
        total += len(text) + 2
    return "\n\n".join(output)


def build_explanation_text(question: dict[str, Any], correct_labels: list[str]) -> list[str]:
    choices = question.get("choiceTextList")
    choice_count = len(choices) if isinstance(choices, list) and choices else 1
    snippets_by_choice = normalize_snippet_list(question.get("explanation_choice_snippets"))
    common_parts = (
        flatten_strings(question.get("explanation_common_prefix"))
        + flatten_strings(question.get("explanation_common_summary"))
    )
    answer_text = str(question.get("answer_result_text") or "").strip()
    explanations: list[str] = []

    for index in range(choice_count):
        snippets = snippets_by_choice[index] if index < len(snippets_by_choice) else []
        label = ""
        if isinstance(correct_labels, list) and index < len(correct_labels):
            label = str(correct_labels[index] or "").strip()
        choice_text = ""
        if isinstance(choices, list) and index < len(choices):
            choice_text = str(choices[index] or "").strip()

        prefix_parts = []
        if label:
            prefix_parts.append(f"選択肢{index + 1}は「{label}」です。")
        if choice_text:
            prefix_parts.append(f"選択肢本文: {choice_text}")

        body = compact_join(snippets or common_parts)
        if not body:
            body = "この選択肢は、正答番号と問題文の条件を照合して判断します。"
        if answer_text:
            body = f"{body}\n\n正答情報: {answer_text}"
        explanations.append("\n".join(prefix_parts + [body]).strip())

    return explanations


def build_question_type_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "questionBodyText": question.get("questionBodyText", ""),
            "choiceTextList": question.get("choiceTextList", []),
            "questionType": question.get("questionType", ""),
            "original_question_id": original_id(question),
            "question_url": question.get("question_url", ""),
        }
        for question in questions
    ]


def build_intent_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions:
        correct_choice_text, changed, detail = resolved_correct_choice_text(question)
        payload.append(
            {
                "questionIntent_changed": False,
                "questionIntent_change_detail": "",
                "original_question_id": original_id(question),
                "questionIntent": question.get("questionIntent", "select_correct"),
                "questionIntent_change_reason": "",
                "correctChoiceText_changed": changed,
                "correctChoiceText_change_detail": detail if changed else "",
                "correctChoiceText_change_reason": (
                    "answer_result_text と questionIntent を正本として correctChoiceText を補正"
                    if changed
                    else ""
                ),
                "correctChoiceText": correct_choice_text,
                "explanation_choice_snippets": normalize_first_snippet_list(
                    question.get("explanation_choice_snippets")
                ),
                "question_url": question.get("question_url", ""),
            }
        )
    return payload


def build_explanation_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions:
        correct_choice_text, _, _ = resolved_correct_choice_text(question)
        payload.append(
            {
                "explanationText": build_explanation_text(question, correct_choice_text),
                "suggestedQuestions": list(SUGGESTED_QUESTIONS),
                "suggestedQuestionDetails": list(SUGGESTED_DETAILS),
                "original_question_id": original_id(question),
                "question_url": question.get("question_url", ""),
                "lawGroundedExplanationNotNeeded": False,
            }
        )
    return payload


def build_question_set_patch(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    entries: list[dict[str, Any]] = []
    mode_counts: Counter[str] = Counter()
    for question in questions:
        question_set_id, mode = classify_question_set(question)
        mode_counts[mode] += 1
        entries.append(
            {
                "questionSetId": question_set_id,
                "original_question_id": original_id(question),
                "question_url": question.get("question_url", ""),
            }
        )
    return entries, mode_counts


def build_category(existing: dict[str, Any], question_set_counts: Counter[str]) -> dict[str, Any]:
    updated = dict(existing)
    folder_counts: Counter[str] = Counter()
    for question_set_id, count in question_set_counts.items():
        folder_id = str(QUESTION_SETS[question_set_id]["folderId"])
        folder_counts[folder_id] += count

    folders: list[dict[str, Any]] = []
    for folder in existing.get("folders", []):
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("folderId") or "")
        count = int(folder_counts.get(folder_id, 0))
        next_folder = dict(folder)
        next_folder["questionCount"] = count
        next_folder["isDeleted"] = count <= 0
        folders.append(next_folder)

    question_sets: list[dict[str, Any]] = []
    for question_set in existing.get("questionSets", []):
        if not isinstance(question_set, dict):
            continue
        question_set_id = str(question_set.get("questionSetId") or "")
        count = int(question_set_counts.get(question_set_id, 0))
        next_question_set = dict(question_set)
        next_question_set["questionCount"] = count
        next_question_set["isDeleted"] = count <= 0
        question_sets.append(next_question_set)

    updated["folders"] = folders
    updated["questionSets"] = question_sets
    return updated


def build_artifacts(*, base_dir: Path, category_json: Path, dry_run: bool) -> None:
    activate_context(category_json)

    files = source_files(base_dir)
    if not files:
        raise FileNotFoundError(f"通関士の source JSON が見つかりません: {base_dir}")

    total_questions = 0
    all_questions: list[dict[str, Any]] = []
    question_set_counts: Counter[str] = Counter()
    classification_mode_counts: Counter[str] = Counter()

    for source_path in files:
        questions = source_questions(source_path)
        all_questions.extend(questions)
        total_questions += len(questions)
        question_set_patch, mode_counts = build_question_set_patch(questions)
        classification_mode_counts.update(mode_counts)
        for entry in question_set_patch:
            question_set_counts[str(entry["questionSetId"])] += 1

        outputs = [
            ("10_questionType_fixed", "questionType_fixed", build_question_type_patch(questions)),
            ("15_correctChoiceText_fixed", "correctChoiceText_fixed", build_intent_patch(questions)),
            ("21_explanationText_added", "explanationText_added", build_explanation_patch(questions)),
            ("22_questionSetId_linked", "questionSetId_linked", question_set_patch),
        ]
        for subdir, tag, payload in outputs:
            write_json(patch_path(source_path, subdir, tag), payload, dry_run=dry_run)

    category = build_category(_ACTIVE_CONTEXT["category"], question_set_counts)
    write_json(category_json, category, dry_run=dry_run)

    subject_counts = Counter(exam_subject(question) for question in all_questions)
    print(f"source files: {len(files)}")
    print(f"source questions: {total_questions}")
    for subject, count in subject_counts.items():
        print(f"{subject}: {count}")
    print(f"classification modes: {dict(classification_mode_counts)}")
    print(f"question sets with counts: {sum(1 for count in question_set_counts.values() if count > 0)}")
    print(f"category: {category_json}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="通関士の 01-04 固定名patchと category.json を生成します。"
    )
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--category-json", type=Path, default=DEFAULT_CATEGORY_JSON)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    build_artifacts(
        base_dir=args.base_dir.expanduser().resolve(),
        category_json=args.category_json.expanduser().resolve(),
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
