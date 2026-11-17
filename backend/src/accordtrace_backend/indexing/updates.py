"""Operator-approved complete manifests; no uploads or inferred amendment effects."""
import re
import uuid
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from sqlalchemy import select,text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from accordtrace_backend.indexing import embedding
from accordtrace_backend.indexing.chunking import fingerprint
from accordtrace_backend.common.core import DomainError, PARSER_VERSION, digest, identity
from accordtrace_backend.ingestion.pipeline import immutable_write, owned
from accordtrace_backend.ingestion.parsing import parse
from accordtrace_backend.persistence.storage import Artifact, Document, Issue, Job, LogicalDocument, Membership, Reference, Snapshot, Span, Term, TermUse


class Entry(BaseModel):
    model_config=ConfigDict(extra='forbid')
    logical_id:str=Field(min_length=1,max_length=80)
    agreement_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    role:Literal['agreement','attachment','amendment']
    parent_id:str|None=None
    root:Literal['maud','imports']='imports'
    file:str=Field(min_length=1,max_length=120)
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    revision:str=Field(min_length=1,max_length=80)


def read_manifest(settings,name):
    if name not in settings.approved_manifests or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',name):
        raise DomainError('manifest_not_approved',403)
    try:
        directory=settings.approved_manifest_dir or settings.backend_root/'configs/manifests'
        path=directory/f'{name}.yaml'
        if path.resolve().parent!=directory.resolve():raise DomainError('manifest_path_invalid')
        value=yaml.safe_load(path.read_text(encoding='utf-8'))
        if set(value)!={'documents'}:raise DomainError('invalid_update_manifest',422)
        entries=[Entry.model_validate(e) for e in value['documents']]
        if not entries or len({e.logical_id for e in entries})!=len(entries):raise DomainError('duplicate_logical_document',422)
        mapping={e.logical_id:e for e in entries}
        for e in entries:
            if e.role=='agreement':
                if e.logical_id!=e.agreement_id or e.parent_id is not None:raise DomainError('invalid_agreement_identity',422)
            elif not e.logical_id.startswith(e.agreement_id+':') or e.parent_id not in mapping or mapping[e.parent_id].agreement_id!=e.agreement_id or mapping[e.parent_id].role!='agreement':
                raise DomainError('attachment_identity_required',422)
            read_entry(settings,e.model_dump())
        return [e.model_dump() for e in sorted(entries,key=lambda e:(e.role!='agreement',e.logical_id))]
    except (OSError,UnicodeError,ValidationError,TypeError,KeyError,yaml.YAMLError):raise DomainError('invalid_update_manifest',422) from None


def read_entry(settings,entry):
    root=settings.source_dir if entry['root']=='maud' else settings.runtime/'imports'
    path=(root/entry['file']).resolve()
    if path.parent!=root.resolve():raise DomainError('source_outside_allowlist',422)
    raw=path.read_bytes()
    if digest(raw)!=entry['sha256']:raise DomainError('source_hash_mismatch')
    return raw,raw.decode('utf-8-sig')


def enqueue_manifest(settings,db_engine,key,name,base_id,verification='live',config_override=None):
    from accordtrace_backend.indexing.pipeline import snapshot_documents
    entries=read_manifest(settings,name)
    if not key or len(key)>160:raise DomainError('invalid_idempotency_key',422)
    if config_override and config_override['embedding']['verification']!='mock':raise DomainError('test_configuration_requires_mock_profile')
    config=config_override or embedding.configuration(settings,verification)
    membership=sorted((identity(e['logical_id'],e['sha256']),e['sha256']) for e in entries)
    config={**config,'source_membership':membership,'source_revisions':sorted((identity(e['logical_id'],e['sha256']),e['revision']) for e in entries),
            'logical_documents':sorted((e['logical_id'],e['agreement_id'],e['role'],e['parent_id']) for e in entries)}
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        db.execute(text('SELECT pg_advisory_xact_lock(71240611)'))
        base=db.get(Snapshot,base_id)
        if base is None or base.status not in ('graph_ready','search_ready'):raise DomainError('base_snapshot_not_ready')
        # Same content/config is an exact no-op even if submitted via a manifest.
        fp=fingerprint(config);sid=identity('search_snapshot',fp)
        payload=dict(configuration=config,base_snapshot_id=base_id)
        old=db.scalar(select(Job).where(Job.idempotency_key==key))
        if old:
            if old.kind!='index' or fingerprint(old.configuration)!=fingerprint(payload):raise DomainError('idempotency_key_conflict')
            return old
        if db.get(Snapshot,sid) is None:
            stored={**config,'manifest':entries}
            db.add(Snapshot(id=sid,fingerprint=fp,parser_version=PARSER_VERSION,configuration=stored,
                            base_snapshot_id=base_id,status='building_search'))
            db.flush()
        job=Job(id=str(uuid.uuid4()),idempotency_key=key,snapshot_id=sid,kind='index',configuration=payload)
        db.add(job);db.flush();return job


def materialize(settings,db_engine,claimed,config):
    job_id,token,sid=claimed
    for entry in config['manifest']:
        raw,content=read_entry(settings,entry)
        vid=identity(entry['logical_id'],entry['sha256'])
        with Session(db_engine) as db:
            if db.get(Artifact,(sid,'source:'+vid)):continue
        parsed=parse(content,vid)
        if not parsed.spans:
            parsed.spans=[dict(id=identity(vid,PARSER_VERSION,'unparsed_root'),kind='clause',label=entry['logical_id'],
                start=0,end=len(content),parent_id=None,role='supplemental_unparsed',text_hash=digest(content.encode()))]
            if not content:raise DomainError('empty_source')
        immutable_write(settings.runtime/'sources'/f"{entry['sha256']}.txt",raw)
        with Session(db_engine) as db,db.begin():
            owned(db,job_id,token,settings.lease_seconds)
            old=db.get(LogicalDocument,entry['logical_id'])
            metadata=dict(id=entry['logical_id'],agreement_id=entry['agreement_id'],role=entry['role'],parent_id=entry['parent_id'],approved=True)
            if old and any(getattr(old,k)!=v for k,v in metadata.items()):raise DomainError('logical_identity_conflict')
            if not old:db.add(LogicalDocument(**metadata));db.flush()
            doc=db.get(Document,vid)
            if doc and (doc.raw_hash!=entry['sha256'] or doc.text!=content):raise DomainError('immutable_source_changed')
            if not doc:
                db.add(Document(id=vid,agreement_id=entry['agreement_id'],logical_document_id=entry['logical_id'],
                    raw_hash=entry['sha256'],text_hash=digest(content.encode()),text=content,
                    source_uri='sources/'+entry['sha256']+'.txt',source_revision=entry['revision'],byte_size=len(raw)))
                db.flush()
            db.execute(insert(Membership).values(snapshot_id=sid,document_id=vid).on_conflict_do_nothing())
            def bulk(model,rows):
                for offset in range(0,len(rows),250):db.execute(insert(model).values(rows[offset:offset+250]).on_conflict_do_nothing())
            bulk(Span,[dict(document_id=vid,parser_version=PARSER_VERSION,**s) for s in parsed.spans])
            bulk(Term,[dict(document_id=vid,**{k:v for k,v in t.items() if k not in ('start','end')}) for t in parsed.terms])
            bulk(Reference,parsed.references);bulk(TermUse,parsed.uses)
            bulk(Issue,[dict(id=i['id'],document_id=vid,parser_version=PARSER_VERSION,detail=i) for i in parsed.issues])
            db.add(Artifact(snapshot_id=sid,stage='source:'+vid,detail=dict(raw_hash=entry['sha256'])))
