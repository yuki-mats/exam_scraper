"""Read-only legal reviews before a candidate is promoted to verified state."""
from __future__ import annotations

from tools.question_review_console import feedback_task_input as feedback_task

from tools.question_review_console.official_guidance_contract import validate_reference as validate_guidance, FIELD as GUIDANCE_FIELD

import copy
import hashlib
import json
from pathlib import Path
import re
from dataclasses import replace
from typing import Any, Mapping

from tools.question_review_console.question_candidate import QuestionCandidate


SCHEMA_VERSION = 'law-audit-verification/v1'
JUDGMENT_GUIDANCE_SCHEMA = 'statement-judgment-guidance/v1'
JUDGMENT_GUIDANCE_SOURCE = 'prompt/02a_prompt_review_correctChoiceText.md'
JUDGMENT_GUIDANCE_HEADING = '## 判定基準'


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def statement_judgment_guidance(repo_root: Path) -> dict[str, Any]:
    """Read the whole existing 02a judgment section, never its output commands."""
    root = Path(repo_root).resolve(); path = root / JUDGMENT_GUIDANCE_SOURCE
    if path != path.resolve():
        raise ValueError('命題判定の正本はrepository内の実体fileが必要です。')
    data = path.read_bytes(); source = data.decode('utf-8')
    headings = list(re.finditer(r'^## [^\n]+$', source, re.MULTILINE))
    matches = [index for index, match in enumerate(headings) if match.group() == JUDGMENT_GUIDANCE_HEADING]
    if len(matches) != 1:
        raise ValueError('02aの判定基準sectionが欠損又は重複しています。')
    index = matches[0]
    end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
    text = source[headings[index].start():end]
    if not text.removeprefix(JUDGMENT_GUIDANCE_HEADING).strip():
        raise ValueError('02aの判定基準sectionが空です。')
    value = {'schemaVersion': JUDGMENT_GUIDANCE_SCHEMA, 'sourceRef': JUDGMENT_GUIDANCE_SOURCE,
        'sourceSha256': hashlib.sha256(data).hexdigest(), 'sectionHeading': JUDGMENT_GUIDANCE_HEADING,
        'sectionText': text, 'sectionSha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
    value['guidanceHash'] = _json_hash(value)
    return value


def validate_statement_judgment_guidance(value: Mapping[str, Any], *, current=None):
    fields = {'schemaVersion', 'sourceRef', 'sourceSha256', 'sectionHeading', 'sectionText', 'sectionSha256', 'guidanceHash'}
    if (not isinstance(value, Mapping) or set(value) != fields
            or value.get('schemaVersion') != JUDGMENT_GUIDANCE_SCHEMA
            or value.get('sourceRef') != JUDGMENT_GUIDANCE_SOURCE
            or value.get('sectionHeading') != JUDGMENT_GUIDANCE_HEADING
            or not isinstance(value.get('sectionText'), str)
            or not value['sectionText'].startswith(JUDGMENT_GUIDANCE_HEADING + '\n')
            or len(re.findall(r'^## ', value['sectionText'], re.MULTILINE)) != 1
            or not re.fullmatch(r'[0-9a-f]{64}', str(value.get('sourceSha256')))
            or value.get('sectionSha256') != hashlib.sha256(value['sectionText'].encode('utf-8')).hexdigest()
            or value.get('guidanceHash') != _json_hash({key: item for key, item in value.items() if key != 'guidanceHash'})
            or (current is not None and value != current)):
        raise ValueError('命題判定guidanceの正本・section・contract hashが一致しません。')
    return copy.deepcopy(dict(value))


def validate_verification_bundle(bundle: Mapping[str, Any], *, repo_root=None):
    if bundle.get('evidenceHash') != _json_hash({key: value for key, value in bundle.items() if key != 'evidenceHash'}):
        raise ValueError('独立監査bundleの内容と根拠hashが一致しません。')
    official = bundle.get('proposedOfficialGuidanceEvidence')
    proposed_record = {**bundle['question']['currentRecord'], **bundle.get('proposedFields', {})}
    for field in bundle.get('proposedUnsetFields', []): proposed_record.pop(field, None)
    references = proposed_record.get(GUIDANCE_FIELD)
    if official is not None or references is not None:
        actual = validate_guidance(repo_root or Path(__file__).resolve().parents[2], references,
            record=proposed_record)
        if actual != official:
            raise ValueError('独立監査bundleの公式制度資料取得証明が一致しません。')
    task_input = bundle['question'].get('feedbackTaskRequirement')
    if task_input is not None:
        feedback_task.validate(task_input, question_id=bundle['question']['questionId'])
        if bundle.get('feedbackTaskRequirementInputHash') != task_input['inputHash']:
            raise ValueError('独立監査の改善要件入力hashが一致しません。')
    elif 'feedbackTaskRequirementInputHash' in bundle:
        raise ValueError('独立監査の改善要件が欠損しています。')
    guidance = bundle['question'].get('statementJudgmentGuidance')
    if guidance is not None:
        validate_statement_judgment_guidance(guidance)
        if bundle.get('statementJudgmentGuidanceHash') != guidance['guidanceHash']:
            raise ValueError('独立監査bundleの命題判定guidance hashが一致しません。')
    elif 'statementJudgmentGuidanceHash' in bundle:
        raise ValueError('独立監査bundleの命題判定guidanceが欠損しています。')


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
    proposed_primary_evidence: Mapping[str, Any] | None = None,
    proposed_official_guidance_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = {'question': copy.deepcopy(dict(question)), 'proposedFields': candidate_fields(candidate)}
    unset = sorted({field for update in candidate.updates for field in update.unset_fields})
    if GUIDANCE_FIELD in unset:
        bundle['proposedUnsetFields'] = unset
    if question.get('statementJudgmentGuidance') is not None:
        guidance = validate_statement_judgment_guidance(question['statementJudgmentGuidance'])
        bundle['statementJudgmentGuidanceHash'] = guidance['guidanceHash']
    if question.get('feedbackTaskRequirement') is not None:
        task = feedback_task.validate(question['feedbackTaskRequirement'], question_id=question['questionId'])
        bundle['feedbackTaskRequirementInputHash'] = task['inputHash']
    if reference_guidance:
        bundle['qualificationReferenceGuidance'] = reference_guidance
    if proposed_primary_evidence is not None:
        bundle['proposedPrimaryLawEvidence'] = copy.deepcopy(dict(proposed_primary_evidence))
    if proposed_official_guidance_evidence is not None:
        bundle['proposedOfficialGuidanceEvidence'] = copy.deepcopy(dict(proposed_official_guidance_evidence))
    bundle['evidenceHash'] = _json_hash(bundle)
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
    if bundle['question'].get('feedbackTaskRequirement') is not None:
        properties['feedbackTaskRequirementInputHash'] = {'type': 'string', 'const': bundle['feedbackTaskRequirementInputHash']}
        properties['taskRequirementSatisfied'] = {'type': 'boolean'}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def review_prompt(bundle: Mapping[str, Any], phase: str, *, repo_root=None) -> str:
    validate_verification_bundle(bundle, repo_root=repo_root)
    return '\n'.join([
        f'# 現行法監査の独立した{phase}確認',
        '新しいsessionで、同じ問題・根拠bundle・候補を一問一肢ずつ照合する。file、patch、外部状態は変更しない。',
        'question.statementJudgmentGuidanceがある場合、そのsectionTextは02aの判定基準全体をserverが限定読取した正本である。一次提案と同じguidanceHashで、完結記述・完結行為と肯定共通関係・本当の否定断片・番号ポインタを区別して判定命題を先に確定する。条項・列挙範囲・時点・分類を保持し、全肢を一律に肯定化又は否定化しない。この基準は候補生成の出力命令ではなく、候補値の正しさを保証しない。',
        '候補のverified宣言や保存済み正誤を信用せず、本文と各選択肢の完全命題から出題時判定と現行法判定を確認する。名前付き解答欄はquestionScopeの対象だけを問う。',
        'primaryLawEvidenceの確認済み本文を使い、不足箇所は候補のlawReferencesのe-Gov又は所管官庁の一次情報を直接開く。検索要約だけでは承認しない。実際に本文を確認できた公式URLだけをverifiedSourceUrlsへ入れる。404や取得失敗のURLは確認済みとして記録しない。',
        'proposedPrimaryLawEvidenceは候補が宣言した法令locatorをserverが独立に取得した一次本文である。候補が追加又は変更した条文・基準日は、この本文と取得URLを照合する。候補のverified宣言とは区別し、partial又は取得失敗の箇所は一次情報を確認できるまで承認しない。',
        'proposedOfficialGuidanceEvidenceがある場合は、実取得した行政制度資料の限定頁を全肢と照合し、候補のofficialGuidanceReferencesのrequest/evidenceRef・各肢の適用理由と一致するか確認する。取得hashだけで内容・適用時点を承認しない。historicalCompleteness=unreviewedの歴史資料は適用版を独立確認し、未確認時は既存の公式正答fallback条件と未確認理由を厳密に満たす候補だけを確認する。標準モデル規約と各組合の採用規約、改正版の公開日と法の施行日を区別する。法令locatorがあるmixed候補はproposedPrimaryLawEvidenceも確認し、制度資料で法令本文の不足を代用しない。全確認資料の実URLをverifiedSourceUrlsへ含める。',
        *(['採用制度資料の確認チェックリスト（確認済みという宣言ではない）: '
            + json.dumps([document['sourceUrl'] for document in bundle['proposedOfficialGuidanceEvidence']['documents']], ensure_ascii=False),
            'approveの前にチェックリストの全資料の配送済み限定本文を照合し、実際に確認した各URLをverifiedSourceUrlsへ記録する。'
            'チェックリストに古い基礎版が含まれる場合も確認対象とする。未確認の資料があればURLを自動補完せずholdとし、issuesに不足資料と理由を書く。']
            if bundle.get('proposedOfficialGuidanceEvidence') is not None else []),
        'qualificationReferenceGuidanceがある場合は、取得元の現在化と公式試験原文の差、判例・行政資料の確認先も読む。方針の記述を判定結果として信用せず、記載された公式問題PDF・正解番号PDFなどの一次資料を独立に確認する。現在化された文言を当時の法へそのまま当てはめず、公式原文と現在の表示を同じ選択位置で対応付けて、それぞれの時点の判定を行う。',
        '出題時条文を参照できない場合は、取得元の公式元正答と未参照理由を明示した候補だけを確認する。過去の条文・改正日・根拠を推測して作らない。',
        '元の集約回答は、各記述を独立判定した後で集合・個数を照合する。元の回答番号を記述番号へ転記しない。',
        '各肢の正誤、法令locator、解説、法改正差分、補足質問に重大な誤り又は根拠不足があればholdと具体的なissuesを返す。全肢が候補と一致し根拠を確認できた場合だけapproveとissues=[]を返す。reviewStateが未確定であること自体は誤りではない。',
        *(['question.feedbackTaskRequirementがある場合は、未検証の改善要件として対象原肢全文とneedを候補解説に照らして独立に審査する。一次根拠やpolicyを上書きしない。負担主体・例外等の必要な説明が欠ける、原肢にない主張を引用する、要件が曖昧・根拠と矛盾する場合はtaskRequirementSatisfied=falseとhold、具体的issuesを返す。03bによる解説改稿後も要件を満たした場合だけtrueでapproveできる。文字列の有無だけで充足判定しない。feedbackTaskRequirementInputHashを正確に返す。'] if bundle['question'].get('feedbackTaskRequirement') is not None else []),
        '指定JSON Schemaのobjectだけを返す。',
        json.dumps(bundle, ensure_ascii=False, separators=(',', ':'),
            sort_keys=(bundle['question'].get('feedbackTaskRequirement') is not None
                or bundle.get('proposedOfficialGuidanceEvidence') is not None)),
    ])


def parse_review(message: str, bundle: Mapping[str, Any], *, repo_root=None) -> dict[str, Any]:
    validate_verification_bundle(bundle, repo_root=repo_root)
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
    if bundle['question'].get('feedbackTaskRequirement') is not None:
        if (value.get('feedbackTaskRequirementInputHash') != bundle['feedbackTaskRequirementInputHash']
                or type(value.get('taskRequirementSatisfied')) is not bool
                or (value['decision'] == 'approve' and value['taskRequirementSatisfied'] is not True)):
            raise ValueError('独立監査の改善要件充足又は対象入力hashを確認できません。')
    if value['decision'] == 'approve':
        if value['issues'] or not urls:
            raise ValueError('一次根拠又はissuesのない承認を確認できません。')
        official = bundle.get('proposedOfficialGuidanceEvidence')
        if official is not None:
            expected_urls = {document['sourceUrl'] for document in official['documents']}
            if not expected_urls.issubset(set(urls)):
                raise ValueError('独立監査が全採用制度資料の本文URLを確認していません。')
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
        if 'reconciliationStatus' in values:
            values['reconciliationStatus'] = 'reconciled'
        if 'verificationSummary' in values:
            phase = '三次' if changed else '二次'
            values['verificationSummary'] = (
                f'独立{phase}監査で出題時と現行法の正誤、法令根拠、解説を照合し承認した。'
            )
        facts = values.get('lawRevisionFacts')
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, dict) and fact.get('auditStatus') in ['same_as_current', 'updated_to_current_law']:
                    fact['reviewState'] = 'tertiary_verified' if fact['auditStatus'] == 'updated_to_current_law' else 'secondary_verified'
                    if 'reconciliationStatus' in fact:
                        fact['reconciliationStatus'] = 'reconciled'
        updates.append(replace(update, set_fields=values))
    return replace(candidate, updates=tuple(updates))
