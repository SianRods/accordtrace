from types import SimpleNamespace as NS

import pytest
from tokenizers import Tokenizer,models,pre_tokenizers,trainers

from accordtrace_backend.indexing.chunking import chunks, fingerprint, partitions
from accordtrace_backend.common.core import DomainError, Settings, digest
from accordtrace_backend.indexing.embedding import Gemini, validate_vector
from accordtrace_backend.retrieval.search import SearchRequest, rrf
from accordtrace_backend.indexing.updates import read_manifest


def tokenizer():
    result=Tokenizer(models.WordPiece(unk_token='[UNK]'))
    result.pre_tokenizer=pre_tokenizers.Whitespace()
    result.train_from_iterator(['alpha beta shall not terminate except consent price agreement contract section'],
        trainers.WordPieceTrainer(vocab_size=200,special_tokens=['[UNK]']))
    return result


def test_chunk_partition_and_exact_unicode_coverage():
    text=('alpha beta shall not terminate except consent.\r\n'*150)+'\U0001f600 contract'
    doc=NS(id='doc',agreement_id='contract_0',text=text)
    span=NS(id='parent',kind='clause',role='operative',parent_id='section',label='1.1',start=0,end=len(text))
    section=NS(id='section',kind='section',role='operative',parent_id=None,label='1.1',start=0,end=len(text))
    nested=NS(id='nested',kind='clause',role='operative',parent_id='parent',label='1.1(a)',start=30,end=90)
    definition=NS(id='definition',kind='clause',role='definition',parent_id='section',label='Price',start=0,end=100)
    spans=[section,span,nested,definition]
    assert partitions(spans)==[span]
    rows=chunks(doc,spans,tokenizer(),'profile')
    assert len(rows)>2 and rows[0]['start']==0 and rows[-1]['end']==len(text)
    assert rows==chunks(doc,spans,tokenizer(),'profile')
    assert all(a['start']<b['start']<=a['end'] for a,b in zip(rows,rows[1:]))
    for row in rows:
        assert row['tokens']<=360
        assert len(row['embedding_input'].encode())<=1536
        assert digest(text[row['start']:row['end']].encode())==row['text_hash']
        assert row['parent_id']=='parent'
    assert {r['id'] for r in rows}.isdisjoint({r['id'] for r in chunks(doc,spans,tokenizer(),'new-profile')})


@pytest.mark.parametrize('value',[[0.0]*768,[float('nan')]*768,[float('inf')]*768,[1.0]*384,[True]*768])
def test_malformed_embedding_rejected(value):
    with pytest.raises(DomainError):validate_vector(value)


def test_normalization_fusion_and_profile_fingerprint():
    normalized=validate_vector([2.0]*768)
    assert sum(v*v for v in normalized)==pytest.approx(1)
    ranking,scores=rrf([['a','a','b'],['b','c']])
    assert ranking[0]=='b' and scores['a']==pytest.approx(1/61)
    assert fingerprint({'a':1,'b':2})==fingerprint({'b':2,'a':1})
    assert fingerprint({'task':'query'})!=fingerprint({'task':'document'})


def test_credentials_scope_and_manifest_guards():
    with pytest.raises(DomainError,match='gemini_credentials_missing'):
        Gemini(Settings(gemini_api_key='')).embed(['test'],'RETRIEVAL_QUERY')
    with pytest.raises(DomainError,match='gemini_free_tier_not_confirmed'):
        Gemini(Settings(gemini_api_key='fake',gemini_free_tier_confirmed=False)).embed(['test'],'RETRIEVAL_QUERY')
    with pytest.raises(DomainError,match='manifest_not_approved'):read_manifest(Settings(),'../private')
    for body in (dict(query='q',agreement_ids=[]),dict(mode='exact',agreement_ids=['contract_0']),
                 dict(query='q',agreement_ids=['contract_0','contract_0']),
                 dict(query='\U0001f600'*500,agreement_ids=['contract_0'])):
        with pytest.raises(ValueError):SearchRequest(**body)


def test_gemini_retry_after_and_standard_api(monkeypatch):
    import httpx
    from google import genai
    from google.genai import errors
    calls=[];waits=[]
    class Client:
        def __init__(self,**kwargs):self.models=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def embed_content(self,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:
                raise errors.APIError(429,{'error':{'message':'test quota'}},
                    response=httpx.Response(429,headers={'retry-after':'7'}))
            return NS(embeddings=[NS(values=[1.0]*768)])
    monkeypatch.setattr(genai,'Client',Client)
    provider=Gemini(Settings(gemini_api_key='synthetic-test-key',gemini_free_tier_confirmed=True))
    monkeypatch.setattr(provider,'wait',lambda value:waits.append(value))
    result=provider.embed(['public fixture'],'RETRIEVAL_DOCUMENT')
    assert len(calls)==2 and any(w>=7 for w in waits)
    assert calls[0]['model']=='gemini-embedding-001' and calls[0]['config'].output_dimensionality==768
    assert sum(v*v for v in result[0])==pytest.approx(1)


def test_gemini_daily_retry_info_stops_repeated_calls(monkeypatch):
    from google import genai
    from google.genai import errors
    calls=[]
    class Client:
        def __init__(self,**kwargs):self.models=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def embed_content(self,**kwargs):
            calls.append(kwargs)
            raise errors.APIError(429,{'error':{'message':'fixture quota','details':[
                {'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'85241s'}]}})
    monkeypatch.setattr(genai,'Client',Client)
    provider=Gemini(Settings(gemini_api_key='synthetic-test-key',gemini_free_tier_confirmed=True))
    monkeypatch.setattr(provider,'wait',lambda seconds:None)
    with pytest.raises(DomainError,match='embedding_quota_exhausted'):provider.embed(['public fixture'],'RETRIEVAL_DOCUMENT')
    assert len(calls)==1
