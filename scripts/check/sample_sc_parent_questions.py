import glob
import json
import random

def extract_sc_parent_samples():
    random.seed(42)  # 固定シード
    
    files = sorted(glob.glob("output/sc/questions_json/*/30_merged_2/question_*_merged_*.json"))
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
            
    print(f"Total SC parent questions: {len(all_parents)}")
    
    with_img = [q for q in all_parents if q.get("questionImageStorageUrls") and len(q.get("questionImageStorageUrls")) > 0]
    without_img = [q for q in all_parents if not q.get("questionImageStorageUrls") or len(q.get("questionImageStorageUrls")) == 0]
    
    print(f"With images: {len(with_img)}")
    print(f"Without images: {len(without_img)}")
    
    sample_img = random.sample(with_img, min(15, len(with_img)))
    sample_no_img = random.sample(without_img, 100 - len(sample_img))
    
    sample_100 = sample_img + sample_no_img
    random.shuffle(sample_100)
    
    out_path = "output/sc_parent_sample_100.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(sample_100, fp, ensure_ascii=False, indent=2)
        
    print(f"Saved 100 SC parent samples to {out_path}")

if __name__ == "__main__":
    extract_sc_parent_samples()
