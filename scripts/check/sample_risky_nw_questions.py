import glob
import json
import random

def extract_risky_samples():
    random.seed(123)  # 再現性のための固定シード
    
    files = sorted(glob.glob("output/nw/questions_json/*/30_merged_2/question_*_merged_*.json"))
    files = [f for f in files if "/old/" not in f]
    
    all_parents = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        lg = data.get("list_group_id")
        qbs = data.get("question_bodies", [])
        for qb in qbs:
            qb["_list_group_id"] = lg
            qb["_source_file"] = f
            all_parents.append(qb)
            
    # リスク要因で分類
    negative_qs = []    # 否定設問（誤っている、適切でない、ないもの）
    fill_blank_qs = []  # 穴埋め・記号組合せ
    calc_qs = []        # 計算問題
    image_qs = []       # 画像問題
    law_qs = []         # 法令・規程
    
    for q in all_parents:
        text = q.get("questionBodyText") or ""
        is_calc = q.get("isCalculationQuestion", False) or any(w in text for w in ["何日", "何秒", "何ビット", "何バイト", "計算", "何万円", "何回", "確率"])
        is_law = q.get("isLawRelated", False) or any(w in text for w in ["法", "基準", "規程", "JIS", "著作権", "特許", "労働者派遣"])
        has_img = bool(q.get("questionImageStorageUrls"))
        is_neg = any(w in text for w in ["誤っている", "適切でない", "ないものは", "除いたものは"])
        is_blank = any(w in text for w in ["に入る", "組合せ", "どれか。〔", "どれか。ここで"]) and any(c in ["ア", "イ", "ウ", "エ"] for c in (q.get("choiceTextList") or []))
        
        q["_risk_tags"] = []
        if is_neg:
            q["_risk_tags"].append("否定設問")
            negative_qs.append(q)
        if is_calc:
            q["_risk_tags"].append("計算問題")
            calc_qs.append(q)
        if has_img:
            q["_risk_tags"].append("画像参照")
            image_qs.append(q)
        if is_blank:
            q["_risk_tags"].append("穴埋め・組合せ")
            fill_blank_qs.append(q)
        if is_law:
            q["_risk_tags"].append("法令・規程")
            law_qs.append(q)

    print(f"Total parent questions: {len(all_parents)}")
    print(f"  否定設問: {len(negative_qs)}")
    print(f"  計算問題: {len(calc_qs)}")
    print(f"  画像参照: {len(image_qs)}")
    print(f"  穴埋め・組合せ: {len(fill_blank_qs)}")
    print(f"  法令・規程: {len(law_qs)}")
    
    # 30問のバランスサンプリング:
    # 否定設問 8問, 計算 8問, 画像 6問, 穴埋め/組合せ 5問, 法令 3問
    selected = []
    selected_ids = set()
    
    def pick(pool, count):
        shuffled = list(pool)
        random.shuffle(shuffled)
        picked = 0
        for item in shuffled:
            qid = item.get("source_question_id")
            if qid not in selected_ids:
                selected.append(item)
                selected_ids.add(qid)
                picked += 1
                if picked >= count:
                    break

    pick(negative_qs, 8)
    pick(calc_qs, 8)
    pick(image_qs, 6)
    pick(fill_blank_qs, 5)
    pick(law_qs, 3)
    
    # 不足分があればランダムに補う
    if len(selected) < 30:
        pick(all_parents, 30 - len(selected))
        
    random.shuffle(selected)
    
    out_path = "output/nw_risky_sample_30.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(selected, fp, ensure_ascii=False, indent=2)
        
    print(f"\nSaved 30 risky samples to {out_path}")

if __name__ == "__main__":
    extract_risky_samples()
