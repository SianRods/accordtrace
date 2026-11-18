import json
from types import SimpleNamespace
import httpx
import pytest
from accordtrace_testbed.client import Backend
from accordtrace_testbed.config import Settings
from accordtrace_testbed.dataset import sha
from accordtrace_testbed.cli import exclusive
from accordtrace_testbed.runner import write
from accordtrace_testbed.report import build


def test_admin_routes_blocked_before_network(monkeypatch):
    b=Backend(Settings(backend_api_token='fixture'))
    monkeypatch.setattr(b.client,'request',lambda *a,**k:pytest.fail('Network forbidden'))
    with pytest.raises(ValueError,match='Administrative'):b.request('POST','/api/v1/admin/ingestions')
    b.close()


def test_quote_integrity_uses_python_code_points(monkeypatch):
    b=Backend(Settings(backend_api_token='fixture'));text='a\U0001f600bc'
    monkeypatch.setattr(b,'request',lambda *a,**k:{'document_version_id':'d','agreement_id':'contract_0','source_hash':'h',
        'start':10,'quote':text,'text_hash':sha(text.encode()),'offset_unit':'unicode_codepoints'})
    citation={'span_id':'s','document_version_id':'d','agreement_id':'contract_0','source_hash':'h',
        'start':11,'end':12,'quote':'\U0001f600','text_hash':sha('\U0001f600'.encode()),'offset_unit':'unicode_codepoints','evidence_id':'E1'}
    result={'scope':{'agreement_ids':['contract_0'],'document_version_ids':['d']},'claims':[{'id':'C1','citations':[citation]}]}
    assert b.citation_checks(result)[0]['valid']
    citation['text_hash']='wrong';assert not b.citation_checks(result)[0]['valid'];b.close()


def test_exclusive_capture_and_atomic_json(tmp_path):
    with exclusive(tmp_path):
        with pytest.raises(FileExistsError):
            with exclusive(tmp_path):pass
        write(tmp_path/'data.json',{'quote':'\U0001f600'})
    assert not (tmp_path/'.writer.lock').exists()
    assert json.loads((tmp_path/'data.json').read_text(encoding='utf-8'))['quote']=='\U0001f600'


def test_no_score_is_fabricated_for_empty_report(tmp_path):
    write(tmp_path/'manifest.json',{'identity':{'mode':'retrieval','snapshot_id':'s','case_ids':['a','b']}})
    value=build(tmp_path)
    assert value['planned_cases']==2 and value['complete_cases']==0 and value['metrics']=={}
    assert value['citation_integrity_micro'] is None


def test_operational_failure_is_not_semantic_abstention(tmp_path):
    write(tmp_path/'manifest.json',{'identity':{'mode':'chat','snapshot_id':'s','case_ids':['a','b','missing']}})
    for id,status in [('a','service_unavailable'),('b','insufficient_evidence')]:
        write(tmp_path/'observations'/(id+'.json'),{'case':{'id':id,'agreement_ids':['contract_0'],
            'review_status':'source_verified','expected_behavior':'answer'},'complete':True,
            'chat':{'run':{'status':'completed','answer_status':status,'result':{'claims':[]}}}})
    report=build(tmp_path)
    assert report['planned_cases']==3 and report['observed_cases']==2
    assert report['metrics']['false_abstention_rate']['mean']==1
    assert report['metrics']['false_abstention_rate']['scored_cases']==1
    assert report['outcomes']['service_unavailable']==1
