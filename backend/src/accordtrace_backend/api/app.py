import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core import DomainError, PARSER_VERSION, Settings, digest
from ..graph import driver
from ..ingestion import enqueue
from ..storage import ActiveSnapshot, Chunk, Document, Job, LogicalDocument, Snapshot, Span, engine
from ..retrieval import SearchPacket,SearchRequest, search, evidence as chunk_evidence
from ..indexing import activate, enqueue_index

LOG=logging.getLogger('accordtrace.requests')
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    LOG.addHandler(handler)
    LOG.propagate=False


class IngestionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    manifest: Literal['initial-six']='initial-six'


def job_view(job):
    return dict(id=job.id,snapshot_id=job.snapshot_id,kind=job.kind,status=job.status,stage=job.stage,
                attempts=job.attempts,error_code=job.error_code)


def create_app(settings: Settings|None=None):
    settings=settings or Settings()
    db_engine=engine(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        db_engine.dispose()

    app=FastAPI(title='AccordTrace evidence backend',version='0.2.0',lifespan=lifespan)
    app.state.settings=settings
    app.state.engine=db_engine

    @app.middleware('http')
    async def request_id(request,call_next):
        rid=str(uuid.uuid4())
        start=time.monotonic()
        response=await call_next(request)
        response.headers['X-Request-ID']=rid
        LOG.info(json.dumps(dict(event='request_complete',request_id=rid,method=request.method,
                 status=response.status_code,duration_ms=round((time.monotonic()-start)*1000))))
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request,exc):
        return JSONResponse(status_code=exc.status,content={'error':{'code':exc.code}})

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request,exc):
        return JSONResponse(status_code=503,content={'error':{'code':'storage_unavailable'}})

    def auth(authorization: Annotated[str|None,Header()]=None):
        configured=settings.api_token.get_secret_value()
        if not configured:
            raise DomainError('api_credentials_not_configured',503)
        if authorization is None or not secrets.compare_digest(authorization,'Bearer '+configured):
            raise DomainError('unauthorized',401)

    def session():
        with Session(db_engine) as db:
            yield db

    @app.get('/health/live')
    def live(): return {'status':'alive'}

    @app.get('/health/ready')
    def ready():
        checks={}
        graph_ready=False
        search_ready=False
        active_id=None
        try:
            with Session(db_engine) as db:
                db.execute(text('SELECT 1'))
                graph_ready=bool(db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.status.in_(('graph_ready','search_ready')))))
                pointer=db.get(ActiveSnapshot,1)
                active_id=pointer.snapshot_id if pointer else None
                active=db.get(Snapshot,active_id) if active_id else None
                search_ready=bool(active and active.search_ready)
            checks['postgres']='ready'
        except Exception: checks['postgres']='unavailable'
        try:
            with driver(settings) as conn:
                conn.verify_connectivity()
            checks['neo4j']='ready'
        except Exception: checks['neo4j']='unavailable'
        try:
            with httpx.Client(timeout=2) as client:
                client.get(settings.qdrant_url+'/readyz').raise_for_status()
            checks['qdrant']='ready'
        except Exception: checks['qdrant']='unavailable'
        ok=all(v=='ready' for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503,content=dict(
            status='ready' if ok else 'unavailable',dependencies=checks,graph_ready=graph_ready,
            search_ready=search_ready and ok,active_snapshot_id=active_id))

    protection=[Depends(auth)]

    @app.get('/api/v1/documents',dependencies=protection)
    def documents(db: Session=Depends(session),offset: int=Query(0,ge=0),limit: int=Query(20,ge=1,le=100)):
        docs=db.scalars(select(Document).join(LogicalDocument,Document.logical_document_id==LogicalDocument.id)
            .where(LogicalDocument.approved.is_(True)).order_by(Document.agreement_id,Document.id).offset(offset).limit(limit))
        return {'items':[dict(id=d.id,agreement_id=d.agreement_id,raw_hash=d.raw_hash,text_hash=d.text_hash,
                    byte_size=d.byte_size,source_revision=d.source_revision) for d in docs],'offset':offset,'limit':limit}

    def document(db,version_id):
        doc=db.get(Document,str(version_id))
        logical=db.get(LogicalDocument,doc.logical_document_id) if doc and doc.logical_document_id else None
        if doc is None or logical is None or not logical.approved:
            raise DomainError('document_not_found',404)
        return doc

    @app.get('/api/v1/documents/{version_id}/sections',dependencies=protection)
    def sections(version_id: uuid.UUID,db: Session=Depends(session),offset: int=Query(0,ge=0),limit: int=Query(50,ge=1,le=200)):
        document(db,version_id)
        rows=db.scalars(select(Span).where(Span.document_id==str(version_id),Span.kind=='section',Span.parser_version==PARSER_VERSION)
                        .order_by(Span.start,Span.id).offset(offset).limit(limit))
        return {'items':[dict(id=s.id,label=s.label,start=s.start,end=s.end,parser_version=s.parser_version) for s in rows],
                'offset':offset,'limit':limit}

    @app.get('/api/v1/evidence/{span_id}',dependencies=protection)
    def evidence(span_id: uuid.UUID,db: Session=Depends(session)):
        s=db.get(Span,str(span_id))
        if s is None: raise DomainError('span_not_found',404)
        d=document(db,s.document_id)
        quote=d.text[s.start:s.end]
        if not (0<=s.start<s.end<=len(d.text)) or digest(quote.encode())!=s.text_hash or digest(d.text.encode())!=d.text_hash:
            raise DomainError('evidence_integrity_failed')
        return dict(id=s.id,document_version_id=d.id,agreement_id=d.agreement_id,source_hash=d.raw_hash,
            text_hash=s.text_hash,offset_unit='unicode_codepoints',start=s.start,end=s.end,quote=quote,
            section_label=s.label,parent_span_id=s.parent_id,role=s.role,parser_version=s.parser_version)

    @app.post('/api/v1/admin/ingestions',status_code=202,dependencies=protection)
    def ingestion(body: IngestionRequest,idempotency_key: Annotated[str,Header(min_length=1,max_length=160)]):
        return job_view(enqueue(settings,db_engine,idempotency_key))

    @app.get('/api/v1/admin/jobs/{job_id}',dependencies=protection)
    def job_status(job_id: uuid.UUID,db: Session=Depends(session)):
        job=db.get(Job,str(job_id))
        if job is None: raise DomainError('job_not_found',404)
        return job_view(job)

    @app.post('/api/v1/search',dependencies=protection,response_model=SearchPacket)
    def retrieve(body: SearchRequest):return search(settings,db_engine,body)

    @app.get('/api/v1/chunks/{chunk_id}/evidence',dependencies=protection)
    def child_evidence(chunk_id:uuid.UUID,db:Session=Depends(session)):
        row=db.get(Chunk,str(chunk_id))
        if row is None:raise DomainError('chunk_not_found',404)
        return chunk_evidence(db,row)

    def snapshot_view(db,row):
        active=db.get(ActiveSnapshot,1)
        return dict(id=row.id,status=row.status,search_ready=row.search_ready,base_snapshot_id=row.base_snapshot_id,
            fingerprint=row.fingerprint,configuration=row.configuration,report=row.report,
            active=bool(active and active.snapshot_id==row.id),generation=active.generation if active else 0)

    @app.get('/api/v1/snapshots',dependencies=protection)
    def snapshots(db:Session=Depends(session),offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
        rows=db.scalars(select(Snapshot).order_by(Snapshot.created_at,Snapshot.id).offset(offset).limit(limit))
        active=db.get(ActiveSnapshot,1)
        return dict(items=[snapshot_view(db,row) for row in rows],offset=offset,limit=limit,
                    active_snapshot_id=active.snapshot_id if active else None,generation=active.generation if active else 0)

    @app.get('/api/v1/snapshots/{snapshot_id}',dependencies=protection)
    def snapshot_status(snapshot_id:uuid.UUID,db:Session=Depends(session)):
        row=db.get(Snapshot,str(snapshot_id))
        if row is None:raise DomainError('snapshot_not_found',404)
        return snapshot_view(db,row)

    class BuildRequest(BaseModel):
        model_config=ConfigDict(extra='forbid')
        base_snapshot_id:uuid.UUID
        manifest_id:str|None=None

    @app.post('/api/v1/admin/index-builds',status_code=202,dependencies=protection)
    def build(body:BuildRequest,idempotency_key:Annotated[str,Header(min_length=1,max_length=160)]):
        if body.manifest_id:
            from ..updates import enqueue_manifest
            return job_view(enqueue_manifest(settings,db_engine,idempotency_key,body.manifest_id,str(body.base_snapshot_id)))
        return job_view(enqueue_index(settings,db_engine,idempotency_key,str(body.base_snapshot_id)))

    class ActivateRequest(BaseModel):
        model_config=ConfigDict(extra='forbid')
        expected_generation:int=Field(ge=0)

    @app.post('/api/v1/admin/snapshots/{snapshot_id}/activate',dependencies=protection)
    def publish(snapshot_id:uuid.UUID,body:ActivateRequest):
        return activate(settings,db_engine,str(snapshot_id),body.expected_generation)

    return app
