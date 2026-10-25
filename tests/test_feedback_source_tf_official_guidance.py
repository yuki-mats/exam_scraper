"""Actual source-TF orchestration with real institutional provider boundaries.

The imported rig injects native approval and transport; no production SDK runs.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from tests.test_feedback_source_tf_delivery import rig, no_live_sdk, transport_rig
from tests import test_official_guidance_evidence as evidence_fixture
from tools.question_bank import feedback_source_tf_delivery as api
from tools.question_review_console import official_guidance_contract as contract
from tools.question_review_console.official_guidance_evidence import OfficialGuidanceEvidenceResolver


def attach_guidance(rig, monkeypatch):
    fixture = evidence_fixture.OfficialGuidanceEvidenceTests(); fixture.setUp()
    request = deepcopy(fixture.request)
    request['choiceBindings'] += [{'choiceIndex': 2, 'currentPages': [5], 'historicalSelections': []}]
    resolver = OfficialGuidanceEvidenceResolver(rig.root, fetcher=fixture.resolver.fetcher,
        extractor=fixture.resolver.extractor, now=fixture.resolver.now)
    scope = contract.source_scope(rig.root, 'sample', {'id': 'ui-q1', 'listGroupId': '2026', **rig.request['source']['binding']})
    delivered = contract.prepare_evidence(rig.root, request, record=rig.current, scope=scope, resolver=resolver)
    refs = {key: delivered[key] for key in ('request', 'evidenceRef')}
    refs['applicabilityByChoice'] = [{'choiceIndex': i, 'currentBasis': '現在資料の条項を照合した。',
        'examTimeBasis': '歴史版の適用は別監査した。'} for i in range(3)]
    rig.current[contract.FIELD] = refs
    rig.current['isLawRelated'] = True
    rig.current['lawGroundedExplanationNotNeeded'] = False
    monkeypatch.setattr(contract, 'OfficialGuidanceEvidenceResolver', lambda *_: resolver)
    from tools.question_review_console import official_guidance_evidence as provider
    monkeypatch.setattr(provider, 'OfficialGuidanceEvidenceResolver', lambda *_: resolver)
    return fixture, resolver, delivered


def test_fresh_real_get_precedes_transport_and_readback_retry_preserves_it(rig, monkeypatch):
    fixture, resolver, delivered = attach_guidance(rig, monkeypatch)
    try:
        plan, approval, _, calls = transport_rig(rig, monkeypatch)
        before = len(fixture.calls)
        result = api.publish(rig.root, rig.path, approval, **rig.options)
        assert len(fixture.calls) == before + 3  # actual index/current/historical GET
        assert result['status'] == 'published_readback_matched' and len(calls) == 1
        wrapper_ref = result['officialGuidanceFreshEvidence']
        wrapper = json.loads((rig.root / wrapper_ref['ref']).read_bytes())
        assert wrapper['pinnedEvidenceHash'] == delivered['receipt']['evidenceHash']
        assert wrapper['contentHash'] == delivered['receipt']['contentHash']
        assert result['officialGuidanceFreshEvidenceHash'] == wrapper['validationHash']
        assert api.publish(rig.root, rig.path, approval, **rig.options) == result
        assert len(fixture.calls) == before + 3 and len(calls) == 1
        payload = calls[0][0][0]
        assert set(payload['questions'][0]) != set(plan['source']['normalProjection'])
        assert contract.FIELD not in json.dumps(payload, ensure_ascii=False)
        assert delivered['evidenceRef']['ref'] not in json.dumps(api._git_source(plan['source']), ensure_ascii=False)
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('failure', ['replaced_pdf', 'fetch_failure', 'interrupted_fetch'])
def test_fresh_source_conflict_or_interruption_never_writes_or_replays(rig, monkeypatch, failure):
    fixture, resolver, _ = attach_guidance(rig, monkeypatch)
    try:
        _, approval, _, calls = transport_rig(rig, monkeypatch)
        if failure == 'replaced_pdf':
            fixture.payloads[evidence_fixture.CURRENT] = replace(fixture.payloads[evidence_fixture.CURRENT], data=b'%PDF-replaced')
        else:
            def fail(*args):
                if failure == 'fetch_failure': raise OSError('retrieval failed')
                raise InterruptedError('retrieval interrupted')
            resolver.fetcher = fail
        with pytest.raises((ValueError, OSError)):
            api.publish(rig.root, rig.path, approval, **rig.options)
        assert calls == []
        assert not (rig.path.parent / 'publication/official-guidance-fresh.json').exists()
        assert not (rig.path.parent / 'publication/commit.json').exists()
        with pytest.raises(api.GateRequired, match='commit_unconfirmed'):
            api.publish(rig.root, rig.path, approval, **rig.options)
        assert calls == []
    finally:
        fixture.doCleanups()
