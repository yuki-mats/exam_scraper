"""Read-only legal reviews before a candidate is promoted to verified state."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping

from tools.question_review_console.question_candidate import QuestionCandidate


SCHEMA_VERSION = 'law-audit-verification/v1'


def question_inputs_from_prompt(prompt: str) -> dict[str, Any]:
    for line in reversed(prompt.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value and all(
            isinstance(item, dict) and 'questionId' in item and 'currentRecord' in item
            for item in value
        ):
            inputs = {item['questionId']: item for item in value}
            if len(inputs) != len(value):
                raise ValueError('独立監査の問題IDが重複しています。')
            return inputs
    raise ValueError('独立監査の一問入力を確認できません。')


def candidate_fields(candidate: QuestionCandidate) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for update in candidate.updates:
        for key, value in update.set_fields.items():
            if key in fields and fields[key] != value:
                raise ValueError(f'監査候補のfieldが矛盾しています: {key}')
            fields[key] = copy.deepcopy(value)
    return fields


def required_review_count(fields: Mapping[str, Any]) -> int:
    """候補の各判定を独立に照合し、必要な監査数を決める。補正しない。"""
    facts = fields.get('lawRevisionFacts')
    facts = facts if isinstance(facts, list) else [facts]
    statuses = {fact.get('auditStatus') for fact in facts if isinstance(fact, Mapping)}
    if not facts or len(statuses) == 0 or any(
        not isinstance(fact, Mapping)
        or fact.get('auditStatus') not in {'same_as_current', 'updated_to_current_law', 'not_law_related'}
        for fact in facts
    ):
        raise ValueError('監査候補の各肢に未確定のauditStatusがあります。一次根拠から各肢を再判定してください。')
    expected = 'updated_to_current_law' if 'updated_to_current_law' in statuses else 'same_as_current'
    if fields.get('auditStatus') != expected:
        raise ValueError('監査候補の問題全体と各肢のauditStatusが一致しません。各肢の差分と全体判定を再確認してください。')
    if fields.get('correctChoiceText') != fields.get('currentLawDecision'):
        raise ValueError('候補の正答と現行法判定が一致しません。本文と各肢の完全命題から両方を再判定してください。')
    for decision, snapshot in [('examTimeDecision', 'examTime'), ('currentLawDecision', 'current')]:
        expected_verdicts = fields.get(decision)
        values = [fact.get(snapshot, {}).get('correctChoiceText') for fact in facts]
        if len(facts) == 1 and isinstance(values[0], list):
            values = values[0]
        if not isinstance(expected_verdicts, list) or values != expected_verdicts:
            raise ValueError(f'監査候補の{decision}と各肢の{snapshot}判定が一致しません。各時点を独立に確認してください。')
    return 2 if expected == 'updated_to_current_law' else 1


def verification_bundle(
    question: Mapping[str, Any], candidate: QuestionCandidate, *,
    reference_guidance: str = '',
) -> dict[str, Any]:
    bundle = {'question': copy.deepcopy(dict(question)), 'proposedFields': candidate_fields(candidate)}
    if reference_guidance:
        bundle['qualificationReferenceGuidance'] = reference_guidance
    bundle['evidenceHash'] = hashlib.sha256(json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return bundle


def review_schema(bundle: Mapping[str, Any]) -> dict[str, Any]:
    question_id = bundle['question']['questionId']
    count = len(bundle['question']['currentRecord']['choiceTextList'])
    verdicts = {'type': 'array', 'minItems': count, 'maxItems': count,
                'items': {'type': 'string', 'enum': ['正しい', '間違い']}}
    properties = {
        'schemaVersion': {'type': 'string', 'const': SCHEMA_VERSION},
        'questionId': {'type': 'string', 'const': question_id},
        'evidenceHash': {'type': 'string', 'const': bundle['evidenceHash']},
        'decision': {'type': 'string', 'enum': ['approve', 'hold']},
        'examTimeDecision': verdicts, 'currentLawDecision': verdicts,
        'issues': {'type': 'array', 'items': {'type': 'string'}},
        'verifiedSourceUrls': {'type': 'array', 'items': {'type': 'string'}},
    }
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def review_prompt(bundle: Mapping[str, Any], phase: str) -> str:
    return '\n'.join([
        f'# 現行法監査の独立した{phase}確認',
        '新しいsessionで、同じ問題・根拠bundle・候補を一問一肢ずつ照合する。file、patch、外部状態は変更しない。',
        '候補のverified宣言や保存済み正誤を信用せず、本文と各選択肢の完全命題から出題時判定と現行法判定を確認する。名前付き解答欄はquestionScopeの対象だけを問う。',
        'primaryLawEvidenceの確認済み本文を使い、不足箇所は候補のlawReferencesのe-Gov又は所管官庁の一次情報を直接開く。検索要約だけでは承認しない。実際に確認した公式URLをverifiedSourceUrlsへ入れる。',
        'qualificationReferenceGuidanceがある場合は、取得元の現在化と公式試験原文の差、判例・行政資料の確認先も読む。方針の記述を判定結果として信用せず、記載された公式問題PDF・正解番号PDFなどの一次資料を独立に確認する。現在化された文言を当時の法へそのまま当てはめず、公式原文と現在の表示を同じ選択位置で対応付けて、それぞれの時点の判定を行う。',
        '出題時条文を参照できない場合は、取得元の公式元正答と未参照理由を明示した候補だけを確認する。過去の条文・改正日・根拠を推測して作らない。',
        '元の集約回答は、各記述を独立判定した後で集合・個数を照合する。元の回答番号を記述番号へ転記しない。',
        '各肢の正誤、法令locator、解説、法改正差分、補足質問に重大な誤り又は根拠不足があればholdと具体的なissuesを返す。全肢が候補と一致し根拠を確認できた場合だけapproveとissues=[]を返す。reviewStateが未確定であること自体は誤りではない。',
        '指定JSON Schemaのobjectだけを返す。',
        json.dumps(bundle, ensure_ascii=False, separators=(',', ':')),
    ])


def parse_review(message: str, bundle: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(message)
    fields = bundle['proposedFields']
    for key, expected in [('schemaVersion', SCHEMA_VERSION), ('questionId', bundle['question']['questionId']), ('evidenceHash', bundle['evidenceHash'])]:
        if value.get(key) != expected:
            raise ValueError(f'独立監査の対象又は入力hashが一致しません: {key}')
    if value.get('decision') not in ['approve', 'hold']:
        raise ValueError('独立監査の判断が不正です。')
    if not isinstance(value.get('issues'), list) or not all(isinstance(x, str) for x in value['issues']):
        raise ValueError('独立監査のissuesが不正です。')
    urls = value.get('verifiedSourceUrls')
    if not isinstance(urls, list) or not all(isinstance(x, str) and x.startswith('https://') for x in urls):
        raise ValueError('独立監査の一次根拠URLが不正です。')
    if value['decision'] == 'approve':
        if value['issues'] or not urls:
            raise ValueError('一次根拠又はissuesのない承認を確認できません。')
        for key in ['examTimeDecision', 'currentLawDecision']:
            if value.get(key) != fields.get(key):
                raise ValueError(f'独立監査と候補の判定が一致しません: {key}')
        if fields.get('correctChoiceText') != fields.get('currentLawDecision'):
            raise ValueError('候補の正答と現行法判定が一致しません。')
    return value


def promote_candidate(candidate: QuestionCandidate, reviews: list[Mapping[str, Any]]) -> QuestionCandidate:
    fields = candidate_fields(candidate)
    required = required_review_count(fields)
    changed = required == 2
    if len(reviews) != required or any(x.get('decision') != 'approve' for x in reviews):
        issues = [str(issue) for x in reviews for issue in x.get('issues', [])]
        return replace(candidate, status='blocked', summary='独立現行法監査で未確定: ' + (' / '.join(issues) or '必要な監査の承認が不足しています。'), updates=())
    state = 'tertiary_verified' if changed else 'secondary_verified'
    updates = []
    for update in candidate.updates:
        values = copy.deepcopy(update.set_fields)
        if 'reviewState' in values:
            values['reviewState'] = state
        facts = values.get('lawRevisionFacts')
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, dict) and fact.get('auditStatus') in ['same_as_current', 'updated_to_current_law']:
                    fact['reviewState'] = 'tertiary_verified' if fact['auditStatus'] == 'updated_to_current_law' else 'secondary_verified'
        updates.append(replace(update, set_fields=values))
    return replace(candidate, updates=tuple(updates))
