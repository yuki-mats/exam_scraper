"""Replay native delegation and reviewer completion, never a self-signed approval.

Native session files are trusted local runtime evidence, not cryptographic
signatures. The caller still checks the current candidate bytes and all gates.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from tools.question_review_console.projection import sha256_json

SCHEMA = 'native-independent-approval/v1'
RESULT_SCHEMA = 'independent-scope-review/v1'
CREATED = 'NATIVE_CANDIDATE_SCOPE='
DELEGATED = 'INDEPENDENT_AUDIT_SCOPE='
NATIVE_SESSION_ROOT = Path.home() / '.codex/sessions'
AUTHORIZATION_TEXT = ('Firestore公開　まで　進めて良いよ。　サブエージェントの承認を得られたら。'
    '承認を得られるまでサイクルを回して、クオリティを担保させた状態で公開する仕組みにしたい。人間は関与しない')


def scope_marker(scope, *, creation=False):
    return (CREATED if creation else DELEGATED) + json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _event(ref, *, production):
    if not isinstance(ref, dict) or set(ref) != {'sessionPath', 'eventLine', 'eventSha256IncludingFinalLf'}:
        raise ValueError('native event reference required')
    path = Path(ref['sessionPath'])
    if not path.is_absolute() or path.absolute() != path.resolve() or not path.is_file():
        raise ValueError('physical native session required')
    if production and not path.is_relative_to(NATIVE_SESSION_ROOT):
        raise ValueError('native Codex session provenance required')
    lines = path.read_bytes().splitlines(keepends=True)
    n = ref['eventLine']
    if type(n) is not int or not 1 <= n <= len(lines):
        raise ValueError('native line number invalid')
    line = lines[n - 1]
    if not line.endswith(b'\n') or hashlib.sha256(line).hexdigest() != ref['eventSha256IncludingFinalLf']:
        raise ValueError('native event bytes differ')
    meta = json.loads(lines[0])
    if meta.get('type') != 'session_meta' or not meta.get('payload', {}).get('id'):
        raise ValueError('native session identity missing')
    return json.loads(line), meta['payload'], lines


def _text(payload):
    return ''.join(c.get('text', '') for c in payload.get('content', []) if c.get('type') in {'input_text', 'output_text'})


def _response(event, kind):
    p = event.get('payload', {})
    if event.get('type') != 'response_item' or p.get('type') != kind:
        raise ValueError('native response item type differs')
    return p


def _output_text(output):
    # Native outputs may be a serialized string or a structured output list.
    if isinstance(output, list):
        return '\n'.join(c.get('text', '') for c in output if isinstance(c, dict))
    return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)


def verify_independent_approval(record, scope, *, production=True):
    required = {'schemaVersion', 'authorization', 'creation', 'delegation', 'delegationResult',
                'delegationActivity', 'reviewerRequest', 'reviewerStarted', 'reviewerFinal', 'reviewerComplete'}
    if not isinstance(record, dict) or set(record) != required or record['schemaVersion'] != SCHEMA:
        raise ValueError('dedicated independent approval evidence required')
    entries = {k: _event(record[k], production=production) for k in required - {'schemaVersion'}}
    auth, author, _ = entries['authorization']
    p = _response(auth, 'message')
    kinds = p.get('internal_chat_message_metadata_passthrough', {}).get('content_item_kinds')
    if p.get('role') != 'user' or kinds != ['user.text'] or _text(p).strip() != AUTHORIZATION_TEXT:
        raise ValueError('direct blanket user authorization required')
    # Forked user history is not an authorization origin.
    source = author.get('source')
    if author.get('parent_thread_id') or (isinstance(source, dict) and source.get('subagent')):
        raise ValueError('authorization must originate in parent user session')
    for key in ('creation', 'delegation', 'delegationResult', 'delegationActivity'):
        if entries[key][1]['id'] != author['id'] or record[key]['sessionPath'] != record['authorization']['sessionPath']:
            raise ValueError('author and delegation session differ')
    if not (record['authorization']['eventLine'] < record['creation']['eventLine'] < record['delegation']['eventLine'] < record['delegationResult']['eventLine']):
        raise ValueError('native author/delegation order differs')
    created, _, lines = entries['creation']
    cp = _response(created, 'custom_tool_call_output')
    markers = [l for l in _output_text(cp.get('output', '')).splitlines() if l.startswith(CREATED)]
    if markers != [scope_marker(scope, creation=True)]:
        raise ValueError('native candidate scope creation binding differs')
    calls = [json.loads(l).get('payload', {}) for l in lines[:record['creation']['eventLine'] - 1]]
    creation_calls = [c for c in calls if c.get('type') == 'custom_tool_call' and c.get('call_id') == cp.get('call_id') and c.get('name') == 'exec' and c.get('namespace') in {None, 'functions'}]
    if (not cp.get('call_id') or len(creation_calls) != 1 or not scope.get('nativeCreationCall')
            or creation_calls[0].get('input') != scope['nativeCreationCall']):
        raise ValueError('native candidate creation tool missing')
    delegation, _, _ = entries['delegation']
    dp = _response(delegation, 'function_call')
    tool = dp.get('name', '').removeprefix('collaboration.')
    if tool not in {'spawn_agent', 'followup_task'} or dp.get('namespace') != 'collaboration' or not dp.get('call_id'):
        raise ValueError('native reviewer delegation required')
    args = json.loads(dp['arguments'])
    author_turn = dp.get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
    if (not author_turn or cp.get('internal_chat_message_metadata_passthrough', {}).get('turn_id') != author_turn
            or creation_calls[0].get('internal_chat_message_metadata_passthrough', {}).get('turn_id') != author_turn):
        raise ValueError('candidate creation and delegation turn differ')
    done = _response(entries['delegationResult'][0], 'function_call_output')
    if done.get('call_id') != dp['call_id']:
        raise ValueError('delegation result call differs')
    final, reviewer, _ = entries['reviewerFinal']
    spawn = reviewer.get('source', {}).get('subagent', {}).get('thread_spawn', {})
    path = spawn.get('agent_path', '')
    target = args.get('target') if tool == 'followup_task' else args.get('task_name')
    if (reviewer['id'] == author['id'] or reviewer.get('parent_thread_id') != author['id']
            or spawn.get('parent_thread_id') != author['id'] or not target
            or target not in {path, path.rsplit('/', 1)[-1]}):
        raise ValueError('independent reviewer native identity differs')
    activity = entries['delegationActivity'][0]
    item = activity.get('payload', {}).get('item', {})
    if (activity.get('type') != 'event_msg' or activity.get('payload', {}).get('type') != 'item_completed'
            or item.get('type') != 'SubAgentActivity' or item.get('id') != dp['call_id']
            or item.get('agent_thread_id') != reviewer['id'] or item.get('agent_path') != path
            or item.get('kind') != ('started' if tool == 'spawn_agent' else 'interacted')
            or record['delegationActivity']['eventLine'] <= record['delegation']['eventLine']):
        raise ValueError('native delegation activity differs')
    for key in ('reviewerRequest', 'reviewerStarted', 'reviewerComplete'):
        if entries[key][1]['id'] != reviewer['id'] or record[key]['sessionPath'] != record['reviewerFinal']['sessionPath']:
            raise ValueError('reviewer event session differs')
    request = _response(entries['reviewerRequest'][0], 'agent_message')
    if request.get('author') != path.rsplit('/', 1)[0] or request.get('recipient') != path:
        raise ValueError('delivered reviewer request identity differs')
    message = args.get('message', '')
    encrypted = [c.get('encrypted_content') for c in request.get('content', []) if c.get('type') == 'encrypted_content']
    if encrypted:
        # Equality verifies the delivered envelope, without finding/decrypting keys.
        if not message.startswith('gAAAA') or encrypted != [message]:
            raise ValueError('delivered encrypted request differs')
    elif scope_marker(scope) not in message or message not in _text(request):
        raise ValueError('delivered exact scope request differs')
    started = entries['reviewerStarted'][0]
    sp = started.get('payload', {})
    parent_turn = dp.get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
    if (started.get('type') != 'event_msg' or sp.get('type') != 'task_started' or not parent_turn
            or sp.get('turn_attribution', {}).get('parent_turn_id') != parent_turn
            or sp.get('root_turn_id') != parent_turn or sp.get('turn_attribution', {}).get('root_turn_id') != parent_turn
            or record['reviewerStarted']['eventLine'] >= record['reviewerRequest']['eventLine']
            or record['reviewerRequest']['eventLine'] >= record['reviewerFinal']['eventLine']):
        raise ValueError('reviewer delegated turn binding differs')
    fp = _response(final, 'message')
    if fp.get('role') != 'assistant' or fp.get('phase') != 'final_answer' or any(c.get('type') != 'output_text' for c in fp.get('content', [])):
        raise ValueError('reviewer final answer required')
    turn = fp.get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
    if request.get('internal_chat_message_metadata_passthrough', {}).get('turn_id') != turn:
        raise ValueError('delivered request and final turn differ')
    complete, cm, _ = entries['reviewerComplete']
    if (not turn or turn != sp.get('turn_id') or cm['id'] != reviewer['id'] or record['reviewerComplete']['sessionPath'] != record['reviewerFinal']['sessionPath']
            or record['reviewerComplete']['eventLine'] <= record['reviewerFinal']['eventLine']
            or complete.get('type') != 'event_msg' or complete.get('payload', {}).get('type') != 'task_complete'
            or complete['payload'].get('root_turn_id') != parent_turn
            or complete['payload'].get('turn_id') != turn or complete['payload'].get('last_agent_message') != _text(fp)):
        raise ValueError('reviewer completed turn binding differs')
    if datetime.fromisoformat(final['timestamp'].replace('Z', '+00:00')) <= datetime.fromisoformat(entries['delegationResult'][0]['timestamp'].replace('Z', '+00:00')):
        raise ValueError('copied or stale reviewer final refused')
    verdict = json.loads(_text(fp))
    if (set(verdict) != {'schemaVersion', 'scope', 'decision', 'unresolvedFindings', 'evidence'}
            or verdict['schemaVersion'] != RESULT_SCHEMA or verdict['scope'] != scope
            or verdict['decision'] != 'approved' or verdict['unresolvedFindings'] != []
            or not isinstance(verdict['evidence'], list) or not verdict['evidence']
            or any(not isinstance(e, str) or not e.strip() for e in verdict['evidence'])):
        raise ValueError('explicit exact-scope approval with zero findings required')
    return {'authorSessionId': author['id'], 'reviewerSessionId': reviewer['id'], 'reviewerAgentPath': path,
            'reviewerTurnId': turn, 'scopeHash': sha256_json(scope), 'resultHash': sha256_json(verdict),
            'nativeEvidenceHash': sha256_json(record)}


def main():
    import argparse
    from tools.question_review_console.formal_correction_save import read_plan, independent_approval_scope
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--formal-save-plan', type=Path, required=True)
    parser.add_argument('--repo-root', type=Path, required=True)
    args = parser.parse_args()
    plan = read_plan(args.repo_root, args.formal_save_plan)
    print(scope_marker(independent_approval_scope(plan, args.formal_save_plan, root=args.repo_root), creation=True))


if __name__ == '__main__':
    main()
