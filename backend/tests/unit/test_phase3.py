import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from accordtrace_backend.chat.repository import LIMITS, Halt, reserve
from accordtrace_backend.chat.schemas import Action, MessageRequest, Plan, provider_schema
from accordtrace_backend.chat.providers import LiveProviders, ProviderFailure, retry_delay
from accordtrace_backend.common.core import Settings


def test_monotonic_global_budget_and_no_reset():
    run=SimpleNamespace(counters={k:0 for k in LIMITS})
    for _ in range(5):reserve(run,'generation')
    with pytest.raises(Halt,match='budget_generation'):reserve(run,'generation')
    assert run.counters['generation']==5
    reserve(run,'correction',2)
    with pytest.raises(Halt):reserve(run,'correction')


def test_strict_schema_selectors_and_scope():
    schema=provider_schema(Plan)
    def check(node):
        if isinstance(node,dict):
            if node.get('type')=='object':
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node.get('properties',{}))
            for value in node.values():check(value)
        elif isinstance(node,list):
            for value in node:check(value)
    check(schema)
    with pytest.raises(ValidationError):Action(kind='search',query=None,section_label=None,term=None,evidence_id=None)
    with pytest.raises(ValidationError):MessageRequest(content='q',agreement_ids=['contract_1'])
    with pytest.raises(ValidationError):MessageRequest(content='q',mode='extractive')
    with pytest.raises(ValidationError):MessageRequest(content=' ')


def test_live_gateway_refuses_missing_confirmation_before_network():
    # Explicit overrides keep this test offline even when the operator configures keys.
    provider=LiveProviders(Settings(groq_api_key='',gemini_api_key='',groq_free_tier_confirmed=False,gemini_free_tier_confirmed=False))
    with pytest.raises(ProviderFailure) as error:provider.generate('planner',{},provider_schema(Plan),1,100)
    assert error.value.code=='chat_credentials_not_configured'
    assert retry_delay({'retry-after':'15'})==15
    assert retry_delay({'retry-after':'garbage'})==60


def test_groq_adapter_disables_retries_and_reasoning(monkeypatch):
    from pydantic import SecretStr
    import groq
    seen={}
    class Client:
        def __init__(self,**kwargs):seen.update(kwargs);self.chat=SimpleNamespace(completions=self)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def create(self,**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"standalone_question":"q","tasks":[],"clarification":"Which clause?"}'))],usage=None)
    monkeypatch.setattr(groq,'Groq',Client)
    settings=Settings(groq_api_key=SecretStr('fixture'),gemini_api_key=SecretStr('fixture'),groq_free_tier_confirmed=True,gemini_free_tier_confirmed=True)
    value,usage=LiveProviders(settings).generate('planner',{'question':'untrusted'},provider_schema(Plan),2,1536)
    assert value['clarification'] and seen['max_retries']==0 and seen['include_reasoning'] is False
    assert seen['model']=='openai/gpt-oss-20b' and 'tools' not in seen


def test_provider_payload_fits_quota_without_losing_draft_citations():
    from accordtrace_backend.chat.providers import fit_payload,request_estimate
    from accordtrace_backend.chat.schemas import Review,provider_schema
    evidence=[dict(evidence_id='E'+str(n),agreement_id='contract_0',section_label='1.1',quote='contract evidence '*90,
        source_hash='a'*64,document_version_id='unneeded-id') for n in range(12)]
    payload=dict(evidence=evidence,history=[],draft={'claims':[{'citations':[{'evidence_id':'E11','quote':'contract evidence'}]}]})
    schema=provider_schema(Review);fitted,limited=fit_payload('reviewer',payload,schema,1536,5000)
    assert request_estimate('reviewer',fitted,schema,1536)<=5000
    assert 'E11' in {e['evidence_id'] for e in fitted['evidence']}
    assert limited['omitted_evidence_ids'] and len(payload['evidence'])==12
    assert all('source_hash' not in item for item in fitted['evidence'])
    with pytest.raises(Halt,match='provider_context_budget'):fit_payload('reviewer',payload,schema,1536,100)
