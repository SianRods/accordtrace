import json
import math
import os
from types import SimpleNamespace as NS
import pytest
os.environ['RAGAS_DO_NOT_TRACK']='true'
pytest.importorskip('ragas')
from accordtrace_testbed.config import Settings
from accordtrace_testbed.judges import GeminiOnly,LocalOllama,adapters


def test_ragas_actual_faithfulness_recall_precision_arithmetic():
    from ragas.metrics import Faithfulness,LLMContextRecall,LLMContextPrecisionWithReference
    from ragas.metrics._faithfulness import NLIStatementOutput
    from ragas.metrics._context_recall import ContextRecallClassification
    from ragas.metrics._context_precision import Verification
    nli=NLIStatementOutput(statements=[{'statement':str(i),'reason':'fixture','verdict':v} for i,v in enumerate([1,0,1])])
    assert Faithfulness()._compute_score(nli)==pytest.approx(2/3)
    classifications=[ContextRecallClassification(statement='fixture',reason='fixture',attributed=v) for v in [1,0,1]]
    assert LLMContextRecall()._compute_score(classifications)==pytest.approx(2/3)
    assert LLMContextPrecisionWithReference()._calculate_average_precision([Verification(reason='fixture',verdict=v) for v in [1,0,1]])==pytest.approx(5/6)
    assert math.isnan(LLMContextRecall()._compute_score([]))
    assert math.isnan(Faithfulness()._compute_score(NLIStatementOutput(statements=[])))


def test_explicit_local_embedding_adapter_never_constructs_cloud_client(monkeypatch,tmp_path):
    from google import genai
    monkeypatch.setattr(genai,'Client',lambda *a,**k:pytest.fail('No cloud client for adapter creation/embeddings'))
    calls=[]
    monkeypatch.setattr(LocalOllama,'embed',lambda self,t:calls.append(t) or [1.,0.])
    settings=Settings(gemini_api_key='fixture',gemini_model='explicit-fixture-model',gemini_free_tier_confirmed=True)
    llm,emb,service=adapters(settings,{'provider':'ollama','model':settings.ollama_model},tmp_path)
    from ragas.metrics import AnswerRelevancy
    assert AnswerRelevancy(llm=llm,embeddings=emb).calculate_similarity('original',['generated'])[0]==pytest.approx(1)
    assert calls==['original','generated'] and service.state['calls']==[]


def test_gemini_cache_budget_and_sdk_retries(monkeypatch,tmp_path):
    from google import genai
    seen=[]
    class Client:
        def __init__(self,**kwargs):seen.append(kwargs);self.models=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def generate_content(self,**kwargs):return NS(text='{"value":"\U0001f600"}',candidates=[NS(finish_reason='STOP')],usage_metadata=None)
    monkeypatch.setattr(genai,'Client',Client)
    s=Settings(gemini_api_key='fixture',gemini_model='fixture',gemini_free_tier_confirmed=True,judge_max_calls=1)
    service=GeminiOnly(s,tmp_path);assert service.generate('p')
    assert seen[0]['http_options'].retry_options.attempts==1
    assert GeminiOnly(s,tmp_path).generate('p') and len(seen)==1
    with pytest.raises(RuntimeError,match='budget_exhausted'):GeminiOnly(s,tmp_path).generate('different')


def test_no_judge_key_or_confirmation_means_no_network(tmp_path):
    with pytest.raises(ValueError,match='Judge needs'):GeminiOnly(Settings(gemini_api_key='',gemini_free_tier_confirmed=False),tmp_path)


def test_actual_ragas_scoring_with_mocked_gemini_only(monkeypatch,tmp_path):
    from google import genai
    from accordtrace_testbed.runner import write
    from accordtrace_testbed.judges import score
    class Client:
        def __init__(self,**kwargs):self.models=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def generate_content(self,**kwargs):
            data={'classifications':[{'statement':str(i),'reason':'fixture','attributed':v} for i,v in enumerate([1,0,1])]}
            return NS(text=json.dumps(data),candidates=[NS(finish_reason='STOP')],usage_metadata=None)
    monkeypatch.setattr(genai,'Client',Client)
    monkeypatch.setattr(LocalOllama,'embed',lambda *a:pytest.fail('Context recall does not need embeddings'))
    write(tmp_path/'manifest.json',{'identity':{'embedding_profile':{'provider':'ollama','model':'embeddinggemma:300m'}}})
    write(tmp_path/'observations/fixture.json',{'case':{'id':'fixture','question':'q','reference':'a,b,c','agreement_ids':['contract_0']},
        'complete':True,'search_packet':{'groups':[{'evidence':[{'quote':'a,b'}]}]}})
    values=score(tmp_path,Settings(gemini_api_key='fixture',gemini_model='fixture',gemini_free_tier_confirmed=True),True,['context_recall'])
    assert values[0]['metrics']['context_recall']=={'status':'scored','value':pytest.approx(2/3)}
    assert values[0]['metrics']['generation_faithfulness']['status']=='unavailable'
