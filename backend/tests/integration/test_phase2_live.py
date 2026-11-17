"""Real PG/Neo4j/Qdrant and explicitly synthetic dense embeddings.

Run with scripts/run_phase2_checks.py: separate PG database, mock-profile
collections and no mutation of the production active pointer. Not a benchmark.
"""
import json
import math
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from fastapi.testclient import TestClient
from qdrant_client import models as qm
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from accordtrace_backend import embedding,graph,indexing,ingestion,vectorstore
from accordtrace_backend.api.app import create_app
from accordtrace_backend.chunking import fingerprint
from accordtrace_backend.core import DomainError,Settings,digest,identity
from accordtrace_backend.retrieval import SearchRequest,search
from accordtrace_backend.storage import (ActiveSnapshot,Artifact,Chunk,Document,EmbeddingCache,
    Job,LogicalDocument,Reference,Resolution,Snapshot,Span,engine)
from accordtrace_backend.updates import enqueue_manifest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.environ.get('ACCORDTRACE_PHASE2_INTEGRATION')!='1',
               reason='Requires isolated Phase 2 Compose checks database')]


class FakeEmbeddings:
    """Hash features exercise vector mechanics without claiming Gemini semantics."""
    def __init__(self):self.completed=[];self.calls=0
    def embed(self,inputs,task):
        self.calls+=1
        values=[]
        for value in inputs:
            vector=[0.0]*768
            for word in re.findall(r'\w+',value.lower()):vector[int(digest(word.encode())[:8],16)%768]+=1
            if not any(vector):vector[0]=1
            values.append(embedding.validate_vector(vector));self.completed.append((task,value))
        return values


def test_full_six_contract_index_search_updates_and_publication(monkeypatch):
    settings=Settings();db_engine=engine(settings)
    assert db_engine.url.database=='accordtrace_phase2_checks','Never use the main database for mock activation checks'
    run=str(uuid.uuid4());raw,fp=ingestion.sources(settings);base_id=identity('snapshot',fp)
    with Session(db_engine) as db:base=db.get(Snapshot,base_id)
    if base is None or base.status!='graph_ready':
        job=ingestion.enqueue(settings,db_engine,'phase2-source-'+run)
        ingestion.process(settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds))
    config=embedding.configuration(settings,'mock')
    config['embedding']['epoch']='test-'+run
    config['test_namespace']=run
    job=indexing.register_build(db_engine,'phase2-build-'+run,base_id,config)
    assert indexing.register_build(db_engine,'phase2-build-'+run,base_id,config).id==job.id
    with pytest.raises(DomainError,match='idempotency_key_conflict'):
        indexing.register_build(db_engine,'phase2-build-'+run,base_id,{**config,'graph':'different'})
    provider=FakeEmbeddings();real_upsert=vectorstore.upsert;writes=0
    def interrupted(*args,**kwargs):
        nonlocal writes
        writes+=1
        if writes==2:raise DomainError('injected_upsert_interruption')
        return real_upsert(*args,**kwargs)
    monkeypatch.setattr(vectorstore,'upsert',interrupted)
    with pytest.raises(DomainError,match='injected_upsert_interruption'):
        indexing.process_index(settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds),provider)
    with Session(db_engine) as db:
        assert db.get(Job,job.id).status=='failed'
        assert not db.get(Snapshot,job.snapshot_id).search_ready
        assert db.get(ActiveSnapshot,1).snapshot_id!=job.snapshot_id
        assert db.scalar(select(func.count()).select_from(EmbeddingCache))>0
    ingestion.retry(db_engine,job.id);monkeypatch.setattr(vectorstore,'upsert',real_upsert)
    indexing.process_index(settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds),provider)
    sid=job.snapshot_id
    with Session(db_engine) as db:
        rows=indexing.snapshot_chunks(db,sid);count=len(rows)
        assert count>1000
        assert db.get(Snapshot,sid).search_ready
        for row in rows:
            assert len(row.embedding_input.encode())<=1536 and row.tokens<=360
    successful_documents=[value for task,value in provider.completed if task=='RETRIEVAL_DOCUMENT']
    assert len(successful_documents)==len(set(successful_documents))
    assert len(successful_documents)==len({r.input_hash for r in rows})
    # An extra acknowledged point must block publication; repair only the test point.
    point=str(uuid.uuid4());name=vectorstore.collection_name(sid)
    with vectorstore.client(settings) as conn:
        conn.upsert(name,points=[qm.PointStruct(id=point,vector={'dense':[1.0]+[0.0]*767,
            'bm25':qm.SparseVector(indices=[],values=[])},payload={})],wait=True)
        with pytest.raises(DomainError,match='unexpected_vector_point'):indexing.validate(settings,db_engine,sid)
        conn.delete(name,qm.PointIdsList(points=[point]),wait=True)
    indexing.validate(settings,db_engine,sid)
    with pytest.raises(DomainError,match='mock_snapshot_cannot_activate'):indexing.activate(settings,db_engine,sid,0)

    exact=search(settings,db_engine,SearchRequest(mode='exact',agreement_ids=['contract_0'],
        defined_term='Per Share Cash Amount',snapshot_id=sid))
    assert any('$115.00' in e['quote'] for g in exact['groups'] for e in g['evidence'])
    assert not any(w['code']=='graph_expansion_unavailable' for w in exact['warnings'])
    assert exact['expanded_count']<=12 and exact['evidence_tokens']<=3000
    hybrid=search(settings,db_engine,SearchRequest(query='merger cash consideration',
        agreement_ids=['contract_0','contract_1'],snapshot_id=sid),provider)
    assert hybrid['retrieval_mode']=='hybrid' and all(g['evidence'] for g in hybrid['groups'])
    for group in hybrid['groups']:
        assert all(e['agreement_id']==group['agreement_id'] for e in group['evidence'])
    lexical=search(settings,db_engine,SearchRequest(query='merger consideration',agreement_ids=['contract_0'],snapshot_id=sid))
    assert lexical['retrieval_mode']=='lexical' and lexical['warnings']
    with pytest.raises(DomainError,match='agreement_outside_snapshot_scope'):
        search(settings,db_engine,SearchRequest(query='q',agreement_ids=['unapproved'],snapshot_id=sid))
    # New projection/config, same input/profile: no document embeddings repeated.
    before=len([x for x in provider.completed if x[0]=='RETRIEVAL_DOCUMENT'])
    second=indexing.register_build(db_engine,'phase2-second-'+run,base_id,{**config,'graph':'test-projection-v2'})
    indexing.process_index(settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds),provider)
    assert len([x for x in provider.completed if x[0]=='RETRIEVAL_DOCUMENT'])==before
    with Session(db_engine) as db:generation=db.get(ActiveSnapshot,1).generation
    first=indexing.activate(settings,db_engine,sid,generation,allow_mock=True)
    def contender():
        try:return indexing.activate(settings,db_engine,second.snapshot_id,first['generation'],allow_mock=True)
        except DomainError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:contender(),range(2)))
    assert sum(isinstance(r,dict) for r in results)==1 and 'activation_generation_conflict' in results
    pinned=search(settings,db_engine,SearchRequest(mode='exact',agreement_ids=['contract_0'],section_label='7.1',snapshot_id=sid))
    assert pinned['snapshot_id']==sid
    with Session(db_engine) as db:generation=db.get(ActiveSnapshot,1).generation
    rolled=indexing.activate(settings,db_engine,sid,generation,allow_mock=True)

    headers={'Authorization':'Bearer '+settings.api_token.get_secret_value()}
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/search',json=dict(mode='exact',agreement_ids=['contract_0'],section_label='7.1'),headers=headers)
        assert response.status_code==200 and response.json()['snapshot_id']==sid
        chunk_id=response.json()['groups'][0]['evidence'][0]['chunk_id']
        assert client.get('/api/v1/chunks/'+chunk_id+'/evidence',headers=headers).status_code==200
        assert client.get('/health/ready').json()['search_ready']
        assert client.get('/api/v1/snapshots/'+sid,headers=headers).json()['active']
        assert client.post('/api/v1/admin/snapshots/'+second.snapshot_id+'/activate',headers=headers,
            json=dict(expected_generation=rolled['generation'])).status_code==409

    # Explicitly approved attachment, same-deal exact literal; historical refs unchanged.
    with Session(db_engine) as db:
        ref=db.scalar(select(Reference).join(Span,Span.id==Reference.source_id).where(Span.parser_version=='structural-v3',
            Reference.status=='missing',Reference.literal.op('~*')(r'^(Schedule|Exhibit|Annex) ')).order_by(Reference.id).limit(1))
        source=db.get(Span,ref.source_id);agreement=db.get(Document,source.document_id).agreement_id
        ref_id,literal=ref.id,ref.literal
    directory=settings.runtime/'test-manifests';directory.mkdir(exist_ok=True,parents=True)
    imports=settings.runtime/'imports';imports.mkdir(exist_ok=True)
    content=(literal+'\nPublic synthetic attachment: closing is subject to a demonstration condition.').encode()
    sha=digest(content);(imports/(sha+'.txt')).write_bytes(content)
    entries=[dict(logical_id=r['name'],agreement_id=r['name'],role='agreement',root='maud',file=r['name']+'.txt',
                  sha256=r['sha'],revision=r['revision']) for r in raw]
    entries.append(dict(logical_id=agreement+':'+literal,agreement_id=agreement,role='attachment',parent_id=agreement,
                        root='imports',file=sha+'.txt',sha256=sha,revision='synthetic-phase2-fixture'))
    manifest='fixture-'+run
    (directory/(manifest+'.yaml')).write_text(yaml.safe_dump(dict(documents=entries)),encoding='utf-8')
    update_settings=settings.model_copy(update=dict(approved_manifests=(manifest,),approved_manifest_dir=directory))
    update=enqueue_manifest(update_settings,db_engine,'update-'+run,manifest,sid,'mock',config)
    indexing.process_index(update_settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds),provider)
    with Session(db_engine) as db:
        assert db.get(Resolution,(update.snapshot_id,ref_id)) is not None
        assert db.get(Resolution,(sid,ref_id)) is None
        assert db.get(Reference,ref_id).status=='missing'
        assert len(indexing.snapshot_documents(db,update.snapshot_id))==7
        assert len(indexing.snapshot_documents(db,sid))==6
    # Complete same-membership manifest has the same fingerprint as the base search config.
    duplicate=enqueue_manifest(update_settings,db_engine,'duplicate-update-'+run,manifest,sid,'mock',config)
    assert duplicate.snapshot_id==update.snapshot_id
    indexing.process_index(update_settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds),provider)
    # Corrected attachment text is a new version; all old offsets remain immutable.
    before_update=len([x for x in provider.completed if x[0]=='RETRIEVAL_DOCUMENT'])
    changed=content+b' Additional public fixture sentence.'
    changed_sha=digest(changed);(imports/(changed_sha+'.txt')).write_bytes(changed)
    entries[-1]={**entries[-1],'file':changed_sha+'.txt','sha256':changed_sha,'revision':'synthetic-corrected-source'}
    changed_name='corrected-'+run
    (directory/(changed_name+'.yaml')).write_text(yaml.safe_dump(dict(documents=entries)),encoding='utf-8')
    changed_settings=update_settings.model_copy(update={'approved_manifests':(manifest,changed_name)})
    version=enqueue_manifest(changed_settings,db_engine,'corrected-'+run,changed_name,update.snapshot_id,'mock',config)
    indexing.process_index(changed_settings,db_engine,ingestion.claim(db_engine,settings.lease_seconds),provider)
    assert len([x for x in provider.completed if x[0]=='RETRIEVAL_DOCUMENT'])==before_update+1
    with Session(db_engine) as db:
        old_ids={d.id for d in indexing.snapshot_documents(db,update.snapshot_id)}
        new_ids={d.id for d in indexing.snapshot_documents(db,version.snapshot_id)}
        assert len(old_ids-new_ids)==1 and len(new_ids-old_ids)==1
        assert db.get(Reference,ref_id).status=='missing'
    report=dict(verification='mock_dense_real_pg_neo4j_qdrant',database=db_engine.url.database,
        snapshot_id=sid,second_snapshot_id=second.snapshot_id,update_snapshot_id=update.snapshot_id,
        corrected_snapshot_id=version.snapshot_id,chunks=count,activation=rolled,exact_evidence_ids=[e['chunk_id'] for g in exact['groups'] for e in g['evidence']],
        source_snapshot_id=base_id,manifest_id=manifest,namespace=run)
    (settings.runtime/'phase2-acceptance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    db_engine.dispose()
