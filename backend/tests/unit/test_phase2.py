from types import SimpleNamespace as NS

import pytest
from tokenizers import Tokenizer,models,pre_tokenizers,trainers

from accordtrace_backend.indexing.chunking import chunks, fingerprint, partitions
from accordtrace_backend.common.core import DomainError, Settings, digest
from accordtrace_backend.indexing.embedding import validate_vector
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
    with pytest.raises(DomainError,match='manifest_not_approved'):read_manifest(Settings(),'../private')
    for body in (dict(query='q',agreement_ids=[]),dict(mode='exact',agreement_ids=['contract_0']),
                 dict(query='q',agreement_ids=['contract_0','contract_0']),
                 dict(query='\U0001f600'*500,agreement_ids=['contract_0'])):
        with pytest.raises(ValueError):SearchRequest(**body)
