"""Explicit manifest ingestion with durable stages and fenced job ownership."""
import json
import threading
import uuid
from collections import Counter
from datetime import timedelta

import yaml
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from . import graph
from .core import DomainError, PARSER_VERSION, Settings, digest, identity
from .parsing import parse
from .storage import (Artifact, Document, Issue, Job, Membership, Reference, Snapshot,
                      Span, Term, TermUse, engine, utcnow)


def sources(settings: Settings):
    manifest=yaml.safe_load(settings.manifest.read_text(encoding='utf-8'))
    expected={f'contract_{i}.txt' for i in range(6)}
    if set(manifest['files'])!=expected or manifest['name']!='initial-six':
        raise DomainError('invalid_initial_manifest',422)
    records=[]
    for filename,sha in sorted(manifest['files'].items()):
        path=(settings.source_dir/filename).resolve()
        if path.parent!=settings.source_dir.resolve():
            raise DomainError('source_outside_allowlist',422)
        raw=path.read_bytes()
        if digest(raw)!=sha:
            raise DomainError('source_hash_mismatch',409)
        records.append(dict(name=filename.removesuffix('.txt'),raw=raw,sha=sha,
                            text=raw.decode('utf-8-sig'),revision=manifest['revision']))
    fingerprint=digest(json.dumps(dict(files=manifest['files'], revision=manifest['revision'],
        parser=PARSER_VERSION,canonicalization='utf8-sig-preserve-newlines-v1'),sort_keys=True).encode())
    return records,fingerprint


def enqueue(settings: Settings, db_engine, key: str) -> Job:
    if not key or len(key)>160:
        raise DomainError('invalid_idempotency_key',422)
    _,fingerprint=sources(settings)
    with Session(db_engine,expire_on_commit=False) as db, db.begin():
        # Serialize snapshot/job registration without racing unique constraints.
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':71240611})
        existing=db.scalar(select(Job).where(Job.idempotency_key==key))
        if existing:
            return existing
        sid=identity('snapshot',fingerprint)
        if not db.get(Snapshot,sid):
            db.add(Snapshot(id=sid,fingerprint=fingerprint,parser_version=PARSER_VERSION))
            db.flush()
        job=Job(id=str(uuid.uuid4()),idempotency_key=key,snapshot_id=sid)
        db.add(job)
        db.flush()
        return job


def claim(db_engine, lease_seconds: int):
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        job=db.scalar(select(Job).where(or_(Job.status=='queued',and_(Job.status=='running',
             Job.lease_until<utcnow()))).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        db.get(Snapshot,job.snapshot_id,with_for_update=True)
        # Only one live job may project a given snapshot.
        competing=db.scalar(select(Job.id).where(Job.snapshot_id==job.snapshot_id,Job.id!=job.id,
            Job.status=='running',Job.lease_until>utcnow()).limit(1))
        if competing:
            return None
        job.status='running'
        job.lease_token=str(uuid.uuid4())
        job.lease_until=utcnow()+timedelta(seconds=lease_seconds)
        job.attempts+=1
        job.error_code=None
        return job.id,job.lease_token,job.snapshot_id


def owned(db: Session, job_id, token, lease_seconds):
    job=db.scalar(select(Job).where(Job.id==job_id).with_for_update())
    if job is None or job.status!='running' or job.lease_token!=token or job.lease_until<=utcnow():
        raise DomainError('job_lease_lost')
    job.lease_until=utcnow()+timedelta(seconds=lease_seconds)
    return job


def retry(db_engine, job_id):
    with Session(db_engine) as db,db.begin():
        job=db.get(Job,job_id,with_for_update=True)
        if job is None: raise DomainError('job_not_found',404)
        if job.status!='failed': raise DomainError('only_failed_jobs_can_retry')
        job.status='queued'
        job.error_code=None


def immutable_write(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=content:
            raise DomainError('immutable_artifact_changed')
        return
    temporary=path.with_name(path.name+'.'+str(uuid.uuid4())+'.tmp')
    temporary.write_bytes(content)
    temporary.replace(path)


def process(settings: Settings, db_engine, claimed):
    job_id,token,sid=claimed

    def heartbeat():
        with Session(db_engine) as db,db.begin():
            owned(db,job_id,token,settings.lease_seconds)

    try:
        source_records,fingerprint=sources(settings)
        with Session(db_engine) as db:
            if db.get(Snapshot,sid).fingerprint!=fingerprint:
                raise DomainError('job_manifest_changed')
        reports={}
        for source in source_records:
            heartbeat()
            vid=identity(source['name'],source['sha'])
            stage='canonical:'+vid
            with Session(db_engine) as db:
                done=db.get(Artifact,(sid,stage))
                if done:
                    reports[source['name']]=done.detail
                    continue
            parsed=parse(source['text'],vid)
            if not any(s['kind']=='section' for s in parsed.spans):
                raise DomainError('no_substantive_sections')
            raw_path=settings.runtime/'sources'/f"{source['sha']}.txt"
            immutable_write(raw_path,source['raw'])
            report=dict(document_version_id=vid,raw_hash=source['sha'],
                sections=sum(s['kind']=='section' for s in parsed.spans),spans=len(parsed.spans),
                terms=len(parsed.terms),uses=len(parsed.uses),references=dict(Counter(r['status'] for r in parsed.references)),
                issues=len(parsed.issues),coverage=parsed.coverage)
            with Session(db_engine) as db,db.begin():
                job=owned(db,job_id,token,settings.lease_seconds)
                db.merge(Document(id=vid,agreement_id=source['name'],raw_hash=source['sha'],
                    text_hash=digest(source['text'].encode()),text=source['text'],source_uri='sources/'+raw_path.name,
                    source_revision=source['revision'],byte_size=len(source['raw'])))
                db.flush()
                db.merge(Membership(snapshot_id=sid,document_id=vid))
                def bulk(model,rows):
                    for offset in range(0,len(rows),500):
                        db.execute(insert(model).values(rows[offset:offset+500]).on_conflict_do_nothing())
                bulk(Span,[dict(document_id=vid,parser_version=PARSER_VERSION,**s) for s in parsed.spans])
                bulk(Term,[dict(document_id=vid,**{k:v for k,v in term.items() if k not in ('start','end')}) for term in parsed.terms])
                bulk(Reference,parsed.references)
                bulk(TermUse,parsed.uses)
                bulk(Issue,[dict(id=i['id'],document_id=vid,parser_version=PARSER_VERSION,detail=i) for i in parsed.issues])
                db.merge(Artifact(snapshot_id=sid,stage=stage,detail=report))
                job.stage=stage
            reports[source['name']]=report
        heartbeat()
        with Session(db_engine) as db:
            nodes,edges=graph.records(db,sid,PARSER_VERSION)
        graph_report=graph.project(settings,sid,nodes,edges,heartbeat)
        immutable_write(settings.runtime/'reports'/f'{sid}.json',
                        json.dumps(dict(documents=reports,graph=graph_report),sort_keys=True,indent=2).encode())
        with Session(db_engine) as db,db.begin():
            job=owned(db,job_id,token,settings.lease_seconds)
            snapshot=db.get(Snapshot,sid)
            snapshot.status='graph_ready'
            snapshot.search_ready=False
            snapshot.report=dict(documents=reports,graph=graph_report)
            db.merge(Artifact(snapshot_id=sid,stage='graph',detail=graph_report))
            job.status='succeeded'
            job.stage='graph_ready'
            job.lease_token=None
            job.lease_until=None
        return True
    except Exception as exc:
        with Session(db_engine) as db,db.begin():
            job=db.get(Job,job_id,with_for_update=True)
            if job and job.lease_token==token and job.status=='running' and job.lease_until>utcnow():
                job.status='failed'
                job.error_code=exc.code if isinstance(exc,DomainError) else type(exc).__name__
                job.lease_until=None
                job.lease_token=None
        raise


def work(settings: Settings, once=True):
    stop=threading.Event()
    import signal
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
    db_engine=engine(settings)
    try:
        while not stop.is_set():
            claimed=claim(db_engine,settings.lease_seconds)
            if claimed:
                process(settings,db_engine,claimed)
            if once: return bool(claimed)
            stop.wait(2)
    finally:
        db_engine.dispose()
