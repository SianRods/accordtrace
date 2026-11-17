import pytest
import httpx
from accordtrace_backend.common.core import Settings,DomainError
from accordtrace_backend.indexing import embedding
from accordtrace_backend.indexing.ollama import Ollama,DOCUMENT_PROMPT,QUERY_PROMPT


def test_mode_selects_provider_without_fallback_or_startup_io(monkeypatch):
    from pydantic import ValidationError
    profile={'provider':'gemini','model':'gemini-embedding-001','dimensions':768,'verification':'live'}
    with pytest.raises(DomainError,match='embedding_mode_mismatch'):embedding.provider_for(Settings(),profile)
    local={**profile,'provider':'ollama'}
    assert isinstance(embedding.provider_for(Settings(),local),Ollama)
    with pytest.raises(ValidationError):Settings(offline_mode=False)
    with pytest.raises(DomainError,match='ollama_url_must_be_local'):Ollama(Settings(ollama_url='https://external.example'))


def test_ollama_pins_digest_prompts_and_disables_truncation(monkeypatch):
    calls=[];state={'digest':'fixture-digest'}
    real_client=httpx.Client
    def handler(request):
        import json
        if request.url.path=='/api/version':return httpx.Response(200,json={'version':'test-version'})
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'embeddinggemma:300m','digest':state['digest']}]})
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['embedding']})
        body=json.loads(request.content);calls.append(body)
        return httpx.Response(200,json={'embeddings':[[1.0]+[0.0]*767 for _ in body['input']]})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    settings=Settings(offline_mode=True);profile=Ollama(settings).describe();provider=Ollama(settings,profile)
    assert len(provider.embed(['source'],'RETRIEVAL_DOCUMENT')[0])==768
    provider.embed(['question'],'RETRIEVAL_QUERY')
    assert calls[0]['input']==[DOCUMENT_PROMPT+'source'] and calls[0]['truncate'] is False
    assert calls[1]['input']==[QUERY_PROMPT+'question']
    state['digest']='changed'
    with pytest.raises(DomainError,match='ollama_model_digest_changed'):provider.embed(['source'],'RETRIEVAL_DOCUMENT')
    assert len(calls)==2


def test_offline_profile_is_separate_and_mock_stays_deterministic(monkeypatch):
    monkeypatch.setattr(embedding,'assets',lambda settings:({},None))
    monkeypatch.setattr(Ollama,'describe',lambda self:{'provider':'ollama','model':'embeddinggemma:300m','model_digest':'fixture','verification':'live'})
    local=embedding.configuration(Settings())
    mock=embedding.configuration(Settings(),'mock')
    assert local['embedding']['provider']=='ollama'
    assert mock['embedding']['provider']=='synthetic'
    assert embedding.fingerprint(local)!=embedding.fingerprint(mock)
    with pytest.raises(DomainError,match='mock_profile_requires_test_provider'):
        embedding.provider_for(Settings(),mock['embedding'])


def test_local_mode_accepts_dotenv_boolean(monkeypatch):
    monkeypatch.setenv('ACCORDTRACE_OFFLINE_MODE','true')
    assert Settings().offline_mode is True
    monkeypatch.setenv('ACCORDTRACE_OFFLINE_MODE','false')
    with pytest.raises(ValueError,match='local-only'):Settings()


def test_local_failure_does_not_fall_back(monkeypatch):
    def unavailable(*args,**kwargs):raise RuntimeError('fixture network failure')
    monkeypatch.setattr(httpx,'Client',unavailable)
    with pytest.raises(DomainError,match='ollama_unavailable'):Ollama(Settings()).describe()
