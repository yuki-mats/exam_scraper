import json
import glob
import sys
import os

def display_questions(base_dir, list_group_id, start_idx=0, count=20):
    merged_files = sorted(glob.glob(os.path.join(base_dir, list_group_id, "30_merged_2", "*_merged_*.json")))
    if not merged_files:
        print(f"No merged files found in {base_dir}/{list_group_id}/30_merged_2")
        return

    all_questions = []
    for mf in merged_files:
        with open(mf, "r", encoding="utf-8") as f:
            data = json.load(f)
            q_list = data.get("question_bodies", []) if isinstance(data, dict) else data
            all_questions.extend(q_list)

    total = len(all_questions)
    print(f"=== {list_group_id} TOTAL QUESTIONS: {total} ===")
    
    end_idx = min(start_idx + count, total)
    for i in range(start_idx, end_idx):
        q = all_questions[i]
        body = q.get("questionBodyText", "").strip()
        choices = q.get("choiceTextList", []) or []
        correct = q.get("correctChoiceText", "")
        q_type = q.get("questionType", "")
        exp_list = q.get("explanationText", []) or []

        print("\n" + "="*80)
        print(f"[{i+1}/{total}] Label: {q.get('questionLabel', '')} | Type: {q_type} | ExamYear: {q.get('examYear')} | Set: {q.get('questionSetId', 'None')}")
        print(f"【問題文】\n{body}")
        
        if q_type == "true_false":
            print(f"\n【各肢の成否判定と解説】({len(choices)} choices)")
            for idx, ch in enumerate(choices):
                ans = correct[idx] if isinstance(correct, list) and idx < len(correct) else "不明"
                exp = exp_list[idx] if idx < len(exp_list) else "(解説なし)"
                print(f"\n  --- [肢 {idx+1}] 判定: 【{ans}】 ---")
                print(f"  選択肢本文: {ch}")
                print(f"  整備後解説: {exp}")
        else:
            in_choices = correct in choices
            print(f"\n【形式: 択一({q_type})】 正答一致: {'OK' if in_choices else 'NG(不一致!)'}")
            print(f"正答: {correct}")
            for idx, ch in enumerate(choices):
                mark = "★[正答]" if ch == correct else f"  [{idx+1}]"
                print(f"  {mark} {ch}")
            exp_text = "\n".join(exp_list) if isinstance(exp_list, list) else exp_list
            print(f"\n【整備後解説】:\n{exp_text}")
            
        print("="*80)

if __name__ == "__main__":
    list_group_id = sys.argv[1] if len(sys.argv) > 1 else "201601"
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    cnt = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    display_questions("output/sg/questions_json", list_group_id, start, cnt)
