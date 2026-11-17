"""Inactive search builds and PostgreSQL-authoritative snapshot publication."""
import json
import uuid
from collections import Counter
from contextlib import contextmanager

from sqlalchemy import select,text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from accordtrace_backend.indexing import embedding
from accordtrace_backend.integrations import graph
from accordtrace_backend.integrations import vectorstore
from accordtrace_backend.indexing.chunking import chunks, fingerprint, partitions
from accordtrace_backend.common.core import DomainError, Settings, digest, identity
from accordtrace_backend.ingestion.pipeline import owned
from accordtrace_backend.persistence.storage import Activation, ActiveSnapshot, Artifact, Chunk, ChunkMembership, Document, Job, LogicalDocument, Membership, Resolution, Snapshot, Span, Reference


def snapshot_documents(db,sid):
    return db.scalars(select(Document).join(Membership,Membership.document_id==Document.id)
                      .where(Membership.snapshot_id==sid).order_by(Document.id)).all()


def snapshot_chunks(db,sid):
    return db.scalars(select(Chunk).join(ChunkMembership,ChunkMembership.chunk_id==Chunk.id)
                      .where(ChunkMembership.snapshot_id==sid).order_by(Chunk.id)).all()


def enqueue_index(settings,db_engine,key,base_snapshot_id,verification='live'):
    config=embedding.configuration(settings,verification)
    return register_build(db_engine,key,base_snapshot_id,config)


def register_build(db_engine,key,base_snapshot_id,config):
    if not key or len(key)>160:raise DomainError('invalid_idempotency_key',422)
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        db.execute(text('SELECT pg_advisory_xact_lock(71240611)'))
        base=db.get(Snapshot,base_snapshot_id)
        if base is None or base.status not in ('graph_ready','search_ready'):raise DomainError('base_snapshot_not_ready')
        docs=snapshot_documents(db,base.id)
        if not docs:raise DomainError('empty_snapshot')
        if base.parser_version!=config['parser']:raise DomainError('parser_profile_mismatch')
        config={**config,'source_membership':sorted((d.id,d.raw_hash) for d in docs),
                'source_revisions':sorted((d.id,d.source_revision) for d in docs),
                'logical_documents':sorted((d.logical_document_id,d.agreement_id,
                    db.get(LogicalDocument,d.logical_document_id).role,db.get(LogicalDocument,d.logical_document_id).parent_id) for d in docs)}
        fp=fingerprint(config);sid=identity('search_snapshot',fp)
        payload=dict(configuration=config,base_snapshot_id=base.id)
        old=db.scalar(select(Job).where(Job.idempotency_key==key))
        if old:
            if old.kind!='index' or fingerprint(old.configuration)!=fingerprint(payload):
                raise DomainError('idempotency_key_conflict')
            return old
        if db.get(Snapshot,sid) is None:
            db.add(Snapshot(id=sid,fingerprint=fp,parser_version=base.parser_version,
                configuration=config,base_snapshot_id=base.id,status='building_search'))
            db.flush()
            for doc in docs:db.add(Membership(snapshot_id=sid,document_id=doc.id))
        job=Job(id=str(uuid.uuid4()),idempotency_key=key,snapshot_id=sid,kind='index',configuration=payload)
        db.add(job);db.flush()
        return job


def process_index(settings,db_engine,claimed,provider=None):
    job_id,token,sid=claimed
    def heartbeat():
        with Session(db_engine) as db,db.begin():owned(db,job_id,token,settings.lease_seconds)
    def stage(name,detail):
        with Session(db_engine) as db,db.begin():
            job=owned(db,job_id,token,settings.lease_seconds)
            db.merge(Artifact(snapshot_id=sid,stage=name,detail=detail));job.stage=name
    try:
        with Session(db_engine) as db:
            snapshot=db.get(Snapshot,sid);config=snapshot.configuration
            if snapshot.search_ready:
                with db.begin_nested():
                    job=owned(db,job_id,token,settings.lease_seconds)
                    job.status='succeeded';job.stage='search_ready';job.lease_token=None;job.lease_until=None
                db.commit();return
        if 'manifest' in config:
            from accordtrace_backend.indexing.updates import materialize
            materialize(settings,db_engine,claimed,config)
        _,tokenizer=embedding.assets(settings,config['assets'])
        from accordtrace_backend.indexing.chunking import CHUNKER
        if config['chunker']!=CHUNKER or config['sparse']!=embedding.SPARSE:raise DomainError('unsupported_index_configuration')
        chunker_hash=fingerprint(dict(chunker=config['chunker'],assets=config['assets']))
        with Session(db_engine) as db:
            docs=snapshot_documents(db,sid)
        for doc in docs:
            heartbeat()
            with Session(db_engine) as db:
                done=db.get(Artifact,(sid,'chunks:'+doc.id))
                if done:continue
                spans=db.scalars(select(Span).where(Span.document_id==doc.id,Span.parser_version==config['parser'])).all()
            rows=chunks(doc,spans,tokenizer,chunker_hash)
            if not rows:raise DomainError('empty_document_chunks')
            with Session(db_engine) as db,db.begin():
                owned(db,job_id,token,settings.lease_seconds)
                for offset in range(0,len(rows),250):
                    part=rows[offset:offset+250]
                    db.execute(insert(Chunk).values(part).on_conflict_do_nothing())
                    db.execute(insert(ChunkMembership).values([dict(snapshot_id=sid,chunk_id=r['id']) for r in part]).on_conflict_do_nothing())
                db.add(Artifact(snapshot_id=sid,stage='chunks:'+doc.id,detail=dict(chunks=len(rows))))
        stage('chunks',dict(chunker_hash=chunker_hash))
        resolve_attachments(db_engine,sid,config['parser'])
        with Session(db_engine) as db:
            nodes,edges=graph.records(db,sid,config['parser'])
        graph_report=graph.project(settings,sid,nodes,edges,heartbeat)
        stage('graph',graph_report)
        with Session(db_engine) as db:
            rows=snapshot_chunks(db,sid)
            docs_by_id={d.id:d for d in snapshot_documents(db,sid)}
            parents={p.id:p for p in db.scalars(select(Span).where(Span.document_id.in_(docs_by_id),Span.parser_version==config['parser']))}
        profile=config['embedding']
        if provider is not None and profile['verification']!='mock':raise DomainError('mock_provider_requires_mock_profile')
        provider=provider or embedding.Gemini(settings)
        sparse=embedding.sparse_model(settings,manifest=config['assets'])
        name=vectorstore.collection_name(sid)
        with vectorstore.client(settings) as conn:
            vectorstore.create(conn,name,profile['dimensions'])
            for offset in range(0,len(rows),16):
                part=rows[offset:offset+16];batch='vectors:'+fingerprint([r.id for r in part])
                with Session(db_engine) as db:
                    if db.get(Artifact,(sid,batch)):continue
                values=[r.embedding_input for r in part]
                # Cache is durable even if the upsert/checkpoint fails.
                dense=embedding.cached_embeddings(db_engine,profile,values,'RETRIEVAL_DOCUMENT',provider,heartbeat)
                sv=list(sparse.passage_embed(values))
                heartbeat()
                vectorstore.upsert(conn,name,sid,part,dense,sv,docs_by_id,parents)
                stage(batch,dict(points=len(part)))
        validate(settings,db_engine,sid,heartbeat,claimed)
        with Session(db_engine) as db,db.begin():
            job=owned(db,job_id,token,settings.lease_seconds)
            job.status='succeeded';job.stage='search_ready';job.lease_token=None;job.lease_until=None
    except Exception as exc:
        from accordtrace_backend.persistence.storage import utcnow
        with Session(db_engine) as db,db.begin():
            job=db.get(Job,job_id,with_for_update=True)
            if job and job.status=='running' and job.lease_token==token and job.lease_until>utcnow():
                job.status='failed';job.error_code=exc.code if isinstance(exc,DomainError) else type(exc).__name__
                job.lease_token=None;job.lease_until=None
        raise


def validate(settings,db_engine,sid,heartbeat=lambda:None,claimed=None):
    with Session(db_engine) as db:
        snapshot=db.get(Snapshot,sid)
        if snapshot is None or not snapshot.configuration:raise DomainError('not_search_snapshot')
        config=snapshot.configuration;docs=snapshot_documents(db,sid);rows=snapshot_chunks(db,sid)
        if fingerprint({k:v for k,v in config.items() if k!='manifest'})!=snapshot.fingerprint:
            raise DomainError('snapshot_configuration_changed')
        if not rows or sorted((d.id,d.raw_hash) for d in docs)!=sorted(tuple(x) for x in config['source_membership']):
            raise DomainError('snapshot_source_membership_mismatch')
        parent_ids=set();all_spans={};by_parent={}
        for row in rows:by_parent.setdefault(row.parent_id,[]).append(row)
        for doc in docs:
            logical=db.get(LogicalDocument,doc.logical_document_id) if doc.logical_document_id else None
            if logical is None or not logical.approved:raise DomainError('source_access_revoked',403)
            raw_path=(settings.runtime/doc.source_uri).resolve()
            if raw_path.parent!=(settings.runtime/'sources').resolve():raise DomainError('invalid_source_uri')
            if not raw_path.exists() or digest(raw_path.read_bytes())!=doc.raw_hash or raw_path.read_bytes().decode('utf-8-sig')!=doc.text:
                raise DomainError('raw_source_integrity_failed')
            if digest(doc.text.encode())!=doc.text_hash:raise DomainError('source_text_corrupt')
            for span in db.scalars(select(Span).where(Span.document_id==doc.id,Span.parser_version==config['parser'])):
                if not 0<=span.start<span.end<=len(doc.text) or digest(doc.text[span.start:span.end].encode())!=span.text_hash:
                    raise DomainError('canonical_span_integrity_failed')
                parent_ids.add(span.id)
                all_spans[span.id]=span
            expected_parents=partitions([s for s in all_spans.values() if s.document_id==doc.id])
            for parent in expected_parents:
                children=sorted(by_parent.get(parent.id,[]),key=lambda c:(c.start,c.end))
                if not children or children[0].start!=parent.start or children[-1].end!=parent.end or any(a.end<b.start for a,b in zip(children,children[1:])):
                    raise DomainError('chunk_coverage_failed')
            expected=db.get(Artifact,(sid,'chunks:'+doc.id))
            if expected is None or expected.detail['chunks']!=sum(r.document_id==doc.id for r in rows):raise DomainError('chunk_count_mismatch')
        by_id={d.id:d for d in docs}
        for row in rows:
            doc=by_id.get(row.document_id);parent=all_spans.get(row.parent_id)
            if doc is None or row.parent_id not in parent_ids or parent.document_id!=doc.id or not parent.start<=row.start<row.end<=parent.end:
                raise DomainError('invalid_chunk_parent')
            if digest(doc.text[row.start:row.end].encode())!=row.text_hash or digest(row.embedding_input.encode())!=row.input_hash:
                raise DomainError('chunk_integrity_failed')
        nodes,edges=graph.records(db,sid,config['parser'])
        unresolved=dict(Counter(db.scalars(select(Reference.status).where(Reference.source_id.in_(parent_ids),Reference.status!='verified'))))
    heartbeat()
    with vectorstore.client(settings) as conn:
        vectorstore.create(conn,vectorstore.collection_name(sid),config['embedding']['dimensions'])
        vector_report=vectorstore.reconcile(conn,vectorstore.collection_name(sid),sid,rows,config['embedding'],by_id)
    with graph.driver(settings) as conn,conn.session() as session:
        actual=session.run('MATCH (n:KBNode {snapshot_id:$sid}) RETURN count(n) AS n',sid=sid).single()['n']
        actual_edges=session.run('MATCH (a:KBNode {snapshot_id:$sid})-[r]->(b:KBNode {snapshot_id:$sid}) RETURN count(r) AS n',sid=sid).single()['n']
        if (actual,actual_edges)!=(len(nodes),len(edges)):raise DomainError('graph_validation_failed')
        graph_ids={r['id'] for r in session.run('MATCH (n:KBNode {snapshot_id:$sid}) RETURN n.canonical_id AS id',sid=sid)}
        if graph_ids!={n['canonical_id'] for n in nodes}:raise DomainError('graph_membership_mismatch')
        actual_edge_ids={(r['kind'],r['id'],r['source'],r['target']) for r in session.run('''
            MATCH (a:KBNode {snapshot_id:$sid})-[r]->(b:KBNode {snapshot_id:$sid})
            RETURN type(r) AS kind,r.reference_id AS id,a.graph_id AS source,b.graph_id AS target''',sid=sid)}
        if actual_edge_ids!={(e['kind'],e['reference_id'],e['source'],e['target']) for e in edges}:
            raise DomainError('graph_edge_membership_mismatch')
    heartbeat()
    report=dict(documents=len(docs),chunks=len(rows),graph=dict(nodes=actual,edges=actual_edges),
                vectors=vector_report,unresolved=unresolved,verification=config['embedding']['verification'])
    with Session(db_engine) as db,db.begin():
        if claimed:owned(db,claimed[0],claimed[1],settings.lease_seconds)
        snapshot=db.get(Snapshot,sid,with_for_update=True)
        snapshot.search_ready=True;snapshot.status='search_ready';snapshot.report=report
        db.merge(Artifact(snapshot_id=sid,stage='validation',detail=report))
    return report


def activate(settings,db_engine,sid,expected_generation,allow_mock=False):
    # Cross-store checks happen before the short publication transaction. Published
    # projections are immutable, so a generation CAS is the publication authority.
    with Session(db_engine) as db:
        candidate=db.get(Snapshot,sid)
        if candidate is None or not candidate.search_ready:raise DomainError('snapshot_not_search_ready')
        if candidate.configuration['embedding']['verification']!='live' and not allow_mock:
            raise DomainError('mock_snapshot_cannot_activate')
    validate(settings,db_engine,sid)
    with Session(db_engine) as db,db.begin():
        pointer=db.get(ActiveSnapshot,1,with_for_update=True)
        if pointer.generation!=expected_generation:raise DomainError('activation_generation_conflict')
        if pointer.snapshot_id==sid:return dict(snapshot_id=sid,generation=pointer.generation)
        previous=pointer.snapshot_id;pointer.snapshot_id=sid;pointer.generation+=1
        db.add(Activation(generation=pointer.generation,snapshot_id=sid,previous_id=previous))
        return dict(snapshot_id=sid,generation=pointer.generation)


def pin(db,sid=None):
    if sid is None:
        active=db.get(ActiveSnapshot,1)
        if active is None or active.snapshot_id is None:raise DomainError('no_active_search_snapshot')
        sid=active.snapshot_id
    snapshot=db.get(Snapshot,sid)
    if snapshot is None or not snapshot.search_ready:raise DomainError('snapshot_not_search_ready')
    return snapshot


def resolve_attachments(db_engine,sid,parser):
    with Session(db_engine) as db,db.begin():
        docs=snapshot_documents(db,sid)
        # Manifest logical IDs may explicitly be "Schedule C" or "Exhibit A".
        attachments={}
        for doc in docs:
            logical=db.get(LogicalDocument,doc.logical_document_id) if doc.logical_document_id else None
            if logical and logical.role=='attachment':
                roots=db.scalars(select(Span).where(Span.document_id==doc.id,Span.parser_version==parser,Span.parent_id.is_(None))).all()
                if len(roots)==1:attachments.setdefault((doc.agreement_id,logical.id.partition(':')[2]),[]).append(roots[0].id)
        for doc in docs:
            span_ids=select(Span.id).where(Span.document_id==doc.id,Span.parser_version==parser)
            for ref in db.scalars(select(Reference).where(Reference.source_id.in_(span_ids),Reference.status=='missing')):
                candidates=attachments.get((doc.agreement_id,ref.literal),[])
                if len(candidates)==1:
                    db.execute(insert(Resolution).values(snapshot_id=sid,reference_id=ref.id,target_id=candidates[0]).on_conflict_do_nothing())
