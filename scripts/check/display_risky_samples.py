import json
import sys

def display(start, count):
    with open("output/nw_risky_sample_30.json", "r", encoding="utf-8") as fp:
        samples = json.load(fp)
        
    labels = ["ア", "イ", "ウ", "エ"]
    
    for i in range(start - 1, min(start - 1 + count, len(samples))):
        q = samples[i]
        qid = q.get("source_question_id")
        lg = q.get("_list_group_id")
        exam_lbl = q.get("examLabel")
        q_lbl = q.get("questionLabel")
        tags = q.get("_risk_tags", [])
        imgs = q.get("questionImageStorageUrls") or []
        choice_imgs = q.get("originalQuestionChoiceImageUrls") or []
        choices = q.get("choiceTextList") or []
        correct = q.get("correctChoiceText") or []
        exp = q.get("explanationText")
        
        print("=" * 80)
        print(f"【ぶれ検証 #{i+1:02d}】 タグ: {tags} | 回: {lg} | {exam_lbl} {q_lbl}")
        if imgs:
            print(f"問題画像: {imgs}")
        if choice_imgs:
            print(f"選択肢画像: {choice_imgs[:2]}...")
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
