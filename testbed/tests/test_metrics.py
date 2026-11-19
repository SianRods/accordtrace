import json
from pathlib import Path
import pytest
from accordtrace_testbed.dataset import Case,Evidence,load,validate_sources,sha
from accordtrace_testbed.config import ROOT,Settings
from accordtrace_testbed.metrics import coverage,retrieval,integrity


def case():
    return Case(id='fixture',question='q',agreement_ids=['contract_0'],category='synthetic',reference='abcd',review_status='source_verified',
        reference_contexts=[Evidence(agreement_id='contract_0',source_hash='a'*64,start=0,end=4,quote='abcd')])


def unit(a,b,agreement='contract_0'):
    return {'agreement_id':agreement,'source_hash':'a'*64,'document_version_id':'fixture','start':a,'end':b}


def packet(items):return {'retrieval_mode':'hybrid','groups':[{'agreement_id':'contract_0','evidence':items}]}


def test_overlapping_ranges_do_not_double_count():
    assert coverage(case().reference_contexts[0],[unit(0,3),unit(1,3)])==0.75
    assert coverage(case().reference_contexts[0],[unit(0,3),unit(1,4)])==1


def test_scope_and_missing_evidence_are_not_similarity_confidence():
    value=retrieval(case(),packet([unit(0,4,'contract_1')]))
    assert value['scope_violation_count']==1 and value['evidence_recall_at_6']==0
    assert retrieval(case(),packet([]))['mrr']==0
    assert integrity([])['citation_integrity'] is None


def test_mrr_and_partial_coverage():
    value=retrieval(case(),packet([unit(10,15),unit(0,3)]))
    assert value['mrr']==0.5 and value['annotated_span_coverage_at_6']==0.75
    assert value['evidence_recall_at_6']==0 and not value['required_evidence_complete']


def test_duplicate_ranges_and_multi_agreement_ranking():
    value=retrieval(case(),packet([unit(0,4),unit(0,4)]))
    assert value['returned_evidence_count']==1
    c=case().model_copy(update={'agreement_ids':['contract_0','contract_1']})
    value=retrieval(c,packet([unit(0,4)]))
    assert value['evidence_recall_at_6'] is None and 'mrr' not in value


def test_gold_source_unicode_hash_and_modified_text(tmp_path):
    text='a\U0001f600bc';raw=text.encode();(tmp_path/'contract_0.txt').write_bytes(raw)
    c=case().model_copy(update={'reference_contexts':[Evidence(agreement_id='contract_0',source_hash=sha(raw),start=1,end=2,quote='\U0001f600')]})
    assert validate_sources([c],tmp_path)['cases']==1
    (tmp_path/'contract_0.txt').write_text('modified')
    with pytest.raises(ValueError,match='mismatch'):validate_sources([c],tmp_path)


def test_six_contract_pilot_is_source_aligned_and_not_expert_labelled():
    cases=load(ROOT/'datasets/pilot.jsonl')
    assert len(cases)==18 and len({c.agreement_ids[0] for c in cases})==6
    assert {c.review_status for c in cases}=={'source_verified'}
    if not (Settings().sources/'contract_0.txt').exists():pytest.skip('Local downloaded MAUD corpus required for source alignment')
    assert validate_sources(cases,Settings().sources)['cases']==18
    c2=next(c for c in cases if c.id=='c2_definition_1')
    assert 'Cash Amount' not in c2.reference


@pytest.mark.parametrize('values',[{'ollama_url':'https://google.example'},{'backend_url':'http://external.example'},
    {'backend_url':'https://example.com?token=private'},{'ollama_url':'http://localhost:11434/api'}])
def test_provider_endpoints_fail_closed(values):
    with pytest.raises(ValueError):Settings(**values)


def test_dataset_forbids_unselected_contracts():
    data=case().model_dump();data['agreement_ids']=['contract_6']
    with pytest.raises(ValueError):Case.model_validate(data)
