"""Bind paraphrased improvement requirements and private source hashes."""
from tools.question_review_console.projection import sha256_json


def task_requirement_binding(connection, task_id):
    task = connection.execute('SELECT title,need,target FROM improvement_tasks WHERE task_id=?', (task_id,)).fetchone()
    if task is None:
        raise ValueError('improvement task not found')
    sources = [dict(r) for r in connection.execute(
        'SELECT candidate_id,question_id,text_hash,review_status FROM ai_question_candidates WHERE improvement_task_id=? ORDER BY candidate_id', (task_id,)).fetchall()]
    if not sources:
        raise ValueError('improvement task source binding missing')
    requirement = {'schemaVersion': 'feedback-task-requirement/v1', 'taskId': task_id,
                   'definition': dict(task), 'sources': sources}
    return {'requirement': requirement, 'requirementHash': sha256_json(requirement)}
