import json
from pathlib import Path

goal_dir = Path('docs/goals/shinkyu-2005-manual-patch-quality')
inventory_path = goal_dir / 'question_inventory.md'
lines = [
    '# 鍼灸師 2005年（第13回）全問題 目視精査・パッチ整備 棚卸し台帳',
    '',
    '| 通番 | ファイル名 | 問題番号 | public_question_id | 問題文冒頭 | 目視精査 | 10 形式 | 15 意図 | 23 正答 | 21 解説 |',
    '|---|---|---|---|---|---|---|---|---|---|'
]

q_idx = 1
for f_idx in range(1, 8):
    fname = f'question_2005_{f_idx}.json'
    fpath = Path(f'output/shinkyu/questions_json/2005/00_source/{fname}')
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    questions = data.get('question_bodies', data)
    for q in questions:
        qlabel = q['questionLabel']
        qid = q['public_question_id']
        qtext = q['questionBodyText'].replace('\n', ' ')
        if len(qtext) > 30:
            qtext = qtext[:30] + '...'
        status = '済' if f_idx == 1 else '未着手'
        lines.append(f'| {q_idx} | `{fname}` | {qlabel} | `{qid}` | {qtext} | {status} | {status} | {status} | {status} | {status} |')
        q_idx += 1

with open(inventory_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

print(f'Regenerated inventory for 2005 with Q1-25 done.')
