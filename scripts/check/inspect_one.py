import json
import sys

with open("output/chintaikanrishi_sample_30.json", "r", encoding="utf-8") as fp:
    samples = json.load(fp)

def show_one(idx):
    if idx < 1 or idx > len(samples):
        print(f"Index out of range: {idx}")
        return
    item = samples[idx - 1]
    yr = item["examYear"]
    lbl = item["questionLabel"]
    sid = item["questionSetId"]
    qtype = item["questionType"]
    qtext = item["questionBodyText"]
    choices = item["choiceTextList"]
    ans = item["correctChoiceText"]
    exp = item["explanationText"]
    rev = item.get("lawRevisionFacts", [])
    refs = item.get("lawReferences", [])

    print("=" * 80)
    print(f"【サンプル #{idx:02d}】 {yr}年 {lbl} (Type: {qtype}, Set: {sid})")
    print(f"法令関連フラグ: {item.get('isLawRelated')}")
    print(f"【親問題文】\n{qtext}")
    print("-" * 80)

    # 肢ごとの表示
    if isinstance(choices, list):
        for c_idx, choice_text in enumerate(choices):
            ans_val = ans[c_idx] if isinstance(ans, list) and c_idx < len(ans) else ans
            exp_val = exp[c_idx] if isinstance(exp, list) and c_idx < len(exp) else (exp if c_idx == 0 else "")
            ref_val = refs[c_idx] if isinstance(refs, list) and c_idx < len(refs) else []
            rev_val = rev[c_idx] if isinstance(rev, list) and c_idx < len(rev) else {}

            print(f"▶ 肢 {c_idx + 1} 【判定: {ans_val}】")
            print(f"  文面: {choice_text}")
            print(f"  解説: {exp_val}")
            if ref_val:
                print(f"  法令参照: {ref_val}")
            if rev_val and isinstance(rev_val, dict) and rev_val.get("differenceFacts"):
                print(f"  法改正差分: {rev_val.get('differenceFacts')}")
                if rev_val.get("notes"):
                    print(f"  法改正メモ: {rev_val.get('notes')}")
            print()
    print("=" * 80)

if __name__ == "__main__":
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    show_one(idx)
