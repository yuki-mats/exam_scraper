import json
import sys

def display(start, count):
    with open("output/nw_parent_sample_100.json", "r", encoding="utf-8") as fp:
        samples = json.load(fp)
        
    labels = ["ア", "イ", "ウ", "エ"]
    
    for i in range(start - 1, min(start - 1 + count, len(samples))):
        q = samples[i]
        qid = q.get("source_question_id")
        lg = q.get("_list_group_id")
        exam_lbl = q.get("examLabel")
        q_lbl = q.get("questionLabel")
        imgs = q.get("questionImageStorageUrls") or []
        choices = q.get("choiceTextList") or []
        correct = q.get("correctChoiceText") or []
        exp = q.get("explanationText")
        
        print("=" * 80)
        print(f"【サンプル #{i+1:02d}】 ID: {qid} | 回: {lg} | {exam_lbl} {q_lbl}")
        if imgs:
            print(f"画像 ({len(imgs)}点): {imgs}")
        print("-" * 40)
        print("【問題文】")
        print(q.get("questionBodyText"))
        print("\n【選択肢】")
        for idx, c in enumerate(choices):
            lbl = labels[idx] if idx < len(labels) else str(idx)
            corr = correct[idx] if idx < len(correct) else ""
            print(f"  {lbl}: ({corr}) {c}")
        print("\n【解説】")
        if isinstance(exp, list):
            for idx, e in enumerate(exp):
                lbl = labels[idx] if idx < len(labels) else str(idx)
                corr = correct[idx] if idx < len(correct) else ""
                print(f"--- 選択肢 {lbl} ({corr}) ---")
                print(f"{e}\n")
        else:
            print(f"{exp}\n")

if __name__ == "__main__":
    s = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    display(s, c)
