import os
import glob
import json
import random

def extract_samples():
    random.seed(42)  # 再現性のための固定シード
    
    files = sorted(glob.glob("output/nw/questions_json/upload_to_firestore/*_firestore_*.json"))
    print(f"Loading {len(files)} upload files...")
    
    all_questions = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        qs = data.get("questions", [])
        for q in qs:
            q["_source_file"] = os.path.basename(f)
            all_questions.append(q)
            
    print(f"Total questions loaded: {len(all_questions)}")
    
    # 画像付き問題と通常問題を分類
    with_img = [q for q in all_questions if q.get("questionImageUrls") and len(q.get("questionImageUrls")) > 0]
    without_img = [q for q in all_questions if not q.get("questionImageUrls") or len(q.get("questionImageUrls")) == 0]
    
    print(f"Questions with images: {len(with_img)}")
    print(f"Questions without images: {len(without_img)}")
    
    # 画像付きから15問、通常から85問を抽出
    sample_img_count = min(15, len(with_img))
    sample_no_img_count = 100 - sample_img_count
    
    sampled_img = random.sample(with_img, sample_img_count)
    sampled_no_img = random.sample(without_img, sample_no_img_count)
    
    sample_100 = sampled_img + sampled_no_img
    random.shuffle(sample_100)
    
    out_path = "output/nw_sample_100.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(sample_100, fp, ensure_ascii=False, indent=2)
        
    print(f"Saved 100 samples to {out_path}")

if __name__ == "__main__":
    extract_samples()
