"""Scoped evidence retrieval. Ranking never implies legal confidence."""
import re
import time
from collections import defaultdict,deque
from typing import Any,Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from qdrant_client import models as qm

from . import embedding,graph,vectorstore
from .core import DomainError,digest
from .indexing import pin,snapshot_documents
from .storage import Chunk,ChunkMembership,Document,LogicalDocument,Reference,Resolution,Span,Term


class SearchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(default='',max_length=1536)
    agreement_ids:list[str]=Field(min_length=1,max_length=30)
    snapshot_id:str|None=None
    mode:Literal['auto','exact','lexical']='auto'
    section_label:str|None=Field(default=None,max_length=120)
    defined_term:str|None=Field(default=None,max_length=120)
    limit:int=Field(default=6,ge=1,le=6)

    @model_validator(mode='after')
    def valid_query(self):
        if len(self.query.encode())>1536:raise ValueError('query exceeds embedding byte limit')
        if len(set(self.agreement_ids))!=len(self.agreement_ids):raise ValueError('duplicate agreement IDs')
        if self.section_label and self.defined_term:raise ValueError('choose section or term')
        if self.mode=='exact' and not (self.section_label or self.defined_term):raise ValueError('exact selector required')
        if self.mode!='exact' and not self.query.strip():raise ValueError('query required')
        return self


class EvidenceUnit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    chunk_id:str
    document_version_id:str
    agreement_id:str
    parent_span_id:str
    start:int
    end:int
    quote:str
    text_hash:str
    source_hash:str
    offset_unit:Literal['unicode_codepoints']
    section_label:str
    role:str
    evidence_url:str
    parent_evidence_url:str
    parent_start:int
    parent_end:int
    context_truncated:bool
    reason:Literal['seed','graph']
    rank_score:float|None=None


class EvidenceGroup(BaseModel):
    agreement_id:str
    evidence:list[EvidenceUnit]


class GraphPath(BaseModel):
    source:str
    target:str
    kind:str
    via:list[str]=Field(default_factory=list)


class UnresolvedDependency(BaseModel):
    reference_id:str
    literal:str
    status:str


class SearchPacket(BaseModel):
    model_config=ConfigDict(extra='forbid')
    snapshot_id:str
    embedding_profile:dict[str,Any]
    retrieval_mode:Literal['exact','hybrid','lexical']
    groups:list[EvidenceGroup]
    graph_paths:list[GraphPath]
    unresolved_dependencies:list[UnresolvedDependency]
    warnings:list[dict[str,str]]
    limits_reached:list[str]
    evidence_tokens:int=Field(ge=0,le=3000)
    seed_count:int=Field(ge=0,le=6)
    expanded_count:int=Field(ge=0,le=12)


def allowed_documents(db,agreements=None):
    query=select(Document).join(LogicalDocument,Document.logical_document_id==LogicalDocument.id).where(LogicalDocument.approved.is_(True))
    if agreements is not None:query=query.where(Document.agreement_id.in_(agreements))
    return db.scalars(query).all()


def rrf(rankings):
    scores=defaultdict(float)
    for ids in rankings:
        for rank,key in enumerate(dict.fromkeys(ids),1):scores[key]+=1/(60+rank)
    return sorted(scores,key=lambda key:(-scores[key],key)),dict(scores)


def evidence(db,chunk):
    doc=db.get(Document,chunk.document_id);parent=db.get(Span,chunk.parent_id)
    logical=db.get(LogicalDocument,doc.logical_document_id) if doc else None
    if doc is None or logical is None or not logical.approved:raise DomainError('document_not_found',404)
    quote=doc.text[chunk.start:chunk.end]
    if not 0<=chunk.start<chunk.end<=len(doc.text) or digest(quote.encode())!=chunk.text_hash or digest(doc.text.encode())!=doc.text_hash:
        raise DomainError('evidence_integrity_failed')
    return dict(chunk_id=chunk.id,document_version_id=doc.id,agreement_id=doc.agreement_id,parent_span_id=parent.id,
        start=chunk.start,end=chunk.end,quote=quote,text_hash=chunk.text_hash,source_hash=doc.raw_hash,
        offset_unit='unicode_codepoints',section_label=parent.label,role=parent.role,
        evidence_url='/api/v1/chunks/'+chunk.id+'/evidence',parent_evidence_url='/api/v1/evidence/'+parent.id,
        parent_start=parent.start,parent_end=parent.end,context_truncated=(chunk.start,chunk.end)!=(parent.start,parent.end))


def search(settings,db_engine,request,provider=None):
    started=time.monotonic();warnings=[];limits=[];paths=[]
    if request.mode=='auto' and not request.section_label and not request.defined_term:
        identifiers=set(re.findall(r'\b[Ss]ection\s+(\d{1,2}\.\d{1,2}(?:\([a-zivx0-9]+\))*)',request.query))
        if len(identifiers)==1:request=request.model_copy(update={'section_label':identifiers.pop()})
    with Session(db_engine) as db:
        snapshot=pin(db,request.snapshot_id);sid=snapshot.id;config=snapshot.configuration
        # Copies of immutable config/IDs leave no transaction open during provider IO.
        docs=[d for d in snapshot_documents(db,sid) if d.agreement_id in request.agreement_ids]
        approved={d.id for d in allowed_documents(db,request.agreement_ids)}
        docs=[d for d in docs if d.id in approved]
        if set(request.agreement_ids)!={d.agreement_id for d in docs}:raise DomainError('agreement_outside_snapshot_scope',403)
        document_ids={d.id for d in docs}
        exact=[]
        if request.section_label:
            targets=db.scalars(select(Span).where(Span.document_id.in_(document_ids),Span.parser_version==snapshot.parser_version,
                Span.label==request.section_label,Span.kind.in_(('section','clause')))).all()
            # Section intro clauses share section labels; prefer actual sections.
            by_doc=defaultdict(list)
            for target in targets:by_doc[target.document_id].append(target)
            targets=[]
            for group in by_doc.values():
                sections=[s for s in group if s.kind=='section'];group=sections or group
                if len(group)>1:raise DomainError('ambiguous_section_selector')
                targets.extend(group)
            exact=targets
        elif request.defined_term:
            terms=db.scalars(select(Term).join(Span,Span.id==Term.span_id).where(Term.document_id.in_(document_ids),
                Span.parser_version==snapshot.parser_version,Term.name==request.defined_term)).all()
            groups=defaultdict(list)
            for term in terms:groups[term.document_id].append(term)
            if any(len(group)>1 for group in groups.values()):raise DomainError('ambiguous_term_selector')
            for term in terms:
                if term.location_only:warnings.append(dict(code='definition_location_only',term_id=term.id))
                exact.append(db.get(Span,term.span_id))
        exact_by_agreement=defaultdict(list)
        for span in exact:
            rows=db.scalars(select(Chunk).join(ChunkMembership,ChunkMembership.chunk_id==Chunk.id).where(
                ChunkMembership.snapshot_id==sid,Chunk.document_id==span.document_id,Chunk.start<span.end,Chunk.end>span.start)
                .order_by(Chunk.start,Chunk.id).limit(6)).all()
            agreement=next(d.agreement_id for d in docs if d.id==span.document_id)
            exact_by_agreement[agreement].extend(r.id for r in rows)
    _,tokenizer=embedding.assets(settings,config['assets'])
    if config['sparse']!=embedding.SPARSE:raise DomainError('unsupported_sparse_profile',503)
    query_vector=None;mode='exact' if request.mode=='exact' else 'hybrid'
    if request.mode=='auto':
        try:
            if config['embedding']['verification']!='live' and provider is None:raise DomainError('mock_profile_requires_test_provider')
            query_vector=embedding.cached_embeddings(db_engine,config['embedding'],[request.query],
                'RETRIEVAL_QUERY',provider or embedding.Gemini(settings))[0]
        except DomainError as exc:
            warnings.append(dict(code=exc.code,component='dense'));mode='lexical'
    elif request.mode=='lexical':mode='lexical'
    ranks={};scores={}
    if request.mode!='exact':
        sparse=embedding.sparse_model(settings,manifest=config['assets'])
        sv=list(sparse.query_embed([request.query]))[0]
        try:
            with vectorstore.client(settings) as conn:
                for agreement in request.agreement_ids:
                    if time.monotonic()-started>=10:
                        limits.append('search_deadline');break
                    filters=vectorstore.scope(sid,[agreement],[d.id for d in docs if d.agreement_id==agreement])
                    bm=conn.query_points(vectorstore.collection_name(sid),query=qm.SparseVector(indices=sv.indices.tolist(),
                        values=sv.values.tolist()),using='bm25',query_filter=filters,limit=20,with_payload=True,
                        timeout=max(1,int(10-(time.monotonic()-started)))).points
                    lists=[[p.payload['chunk_id'] for p in bm]]
                    if query_vector is not None:
                        dense=conn.query_points(vectorstore.collection_name(sid),query=query_vector,using='dense',
                            query_filter=filters,limit=20,search_params=qm.SearchParams(exact=True),with_payload=True,
                            timeout=max(1,int(10-(time.monotonic()-started)))).points
                        lists.append([p.payload['chunk_id'] for p in dense])
                    ranked,values=rrf(lists)
                    ranks[agreement]=list(dict.fromkeys(exact_by_agreement[agreement]+ranked));scores.update(values)
        except Exception:raise DomainError('search_store_unavailable',503) from None
    else:ranks={a:exact_by_agreement[a] for a in request.agreement_ids}
    selected=[];used_parents=set();used_ranges=defaultdict(list);queues={a:deque(ids) for a,ids in ranks.items()}
    token_budget=3000;consumed=0
    with Session(db_engine) as db:
        def add(chunk_id,reason):
            nonlocal consumed
            row=db.get(Chunk,chunk_id)
            if row is None or row.document_id not in document_ids or db.get(ChunkMembership,(sid,row.id)) is None:
                raise DomainError('retrieval_scope_integrity_failed')
            if any(row.start<b and row.end>a for a,b in used_ranges[row.document_id]):return False
            packet=evidence(db,row)
            count=len(tokenizer.encode(packet['quote'],add_special_tokens=False).ids)
            if consumed+count>token_budget:
                if 'evidence_token_budget' not in limits:limits.append('evidence_token_budget')
                return False
            consumed+=count;used_ranges[row.document_id].append((row.start,row.end));used_parents.add(row.parent_id)
            packet.update(reason=reason,rank_score=scores.get(row.id))
            selected.append(packet);return True
        # Round robin prevents a single agreement crowding out explicit comparisons.
        while len(selected)<request.limit and any(queues.values()):
            for agreement in request.agreement_ids:
                while queues.get(agreement) and not add(queues[agreement].popleft(),'seed'):pass
                if len(selected)>=request.limit:break
        seeds=list(selected);unresolved=[]
        if time.monotonic()-started>10:limits.append('search_deadline')
        elif seeds:
            try:
                expanded,graph_paths,gaps,limited=expand_graph(settings,sid,seeds,document_ids,started+10)
                paths.extend(graph_paths);unresolved.extend(gaps)
                if limited:limits.append('graph_unit_budget')
                for span_id in expanded:
                    if time.monotonic()-started>10:
                        limits.append('search_deadline');break
                    span=db.get(Span,span_id)
                    if span is None or span.document_id not in document_ids:raise DomainError('graph_scope_integrity_failed')
                    candidates=db.scalars(select(Chunk).join(ChunkMembership,ChunkMembership.chunk_id==Chunk.id).where(
                        ChunkMembership.snapshot_id==sid,Chunk.document_id==span.document_id,Chunk.start<span.end,Chunk.end>span.start)
                        .order_by(Chunk.start,Chunk.id).limit(4)).all()
                    for row in candidates:
                        if add(row.id,'graph'):break
            except DomainError:raise
            except Exception:warnings.append(dict(code='graph_expansion_unavailable',component='neo4j'))
        if any(q for q in queues.values()):limits.append('seed_limit')
        # Reconcile dependency metadata against canonical mentions for every
        # returned excerpt, including graph additions. A graph outage or hop
        # limit must not make a known missing reference silently disappear.
        resolutions={r.reference_id:r.target_id for r in db.scalars(select(Resolution).where(Resolution.snapshot_id==sid))}
        mentions=db.execute(select(Reference,Span.document_id).join(Span,Span.id==Reference.source_id).where(
            Span.document_id.in_(document_ids),Span.parser_version==config['parser']).order_by(Reference.start,Reference.id)).all()
        canonical_gaps={}
        for mention,source_document_id in mentions:
            if not any(e['document_version_id']==source_document_id and e['start']<=mention.start<e['end'] for e in selected):continue
            target_id=resolutions.get(mention.id,mention.target_id)
            status=mention.status
            if target_id:
                target=db.get(Span,target_id)
                if target is None:raise DomainError('reference_integrity_failed')
                if target.document_id not in document_ids:status='access_restricted'
                elif any(e['document_version_id']==target.document_id and e['start']<target.end and e['end']>target.start for e in selected):continue
                else:status='not_expanded'
            canonical_gaps[mention.id]=dict(reference_id=mention.id,literal=mention.literal,status=status)
        if len(canonical_gaps)>30:limits.append('unresolved_dependency_budget')
        unresolved=list(canonical_gaps.values())[:30]
        return SearchPacket.model_validate(dict(snapshot_id=sid,embedding_profile=config['embedding'],retrieval_mode=mode,
            groups=[dict(agreement_id=a,evidence=[e for e in selected if e['agreement_id']==a]) for a in request.agreement_ids],
            graph_paths=paths,unresolved_dependencies=unresolved,warnings=warnings,limits_reached=list(dict.fromkeys(limits)),
            evidence_tokens=consumed,seed_count=len(seeds),expanded_count=len(selected)-len(seeds))).model_dump()


def expand_graph(settings,sid,seeds,document_ids,deadline=None):
    targets=[];paths=[];gaps=[];seen=set();limited=False
    # REFERENCES is one hop; USES_TERM -> incoming DEFINES is exactly two.
    query='''MATCH (source:KBNode {snapshot_id:$sid})
      WHERE source.document_version_id=$doc AND source.start < $end AND source.end > $start
      CALL (source) {
        MATCH (source)-[r:REFERENCES]->(target:KBNode)
        RETURN target, 'REFERENCES' AS kind, r.start AS occurrence, [] AS via
        UNION ALL
        MATCH (source)-[r:REFERENCES]->(middle:KBNode)-[:REFERENCES]->(target:KBNode)
        WHERE middle.snapshot_id=$sid AND middle.document_version_id IN $documents
          AND middle.agreement_id=source.agreement_id
        RETURN target, 'REFERENCES/REFERENCES' AS kind, r.start AS occurrence,[middle.canonical_id] AS via
        UNION ALL
        MATCH (source)-[u:USES_TERM]->(term:DefinedTerm)<-[:DEFINES]-(target:KBNode)
        RETURN target, 'USES_TERM/DEFINES' AS kind, u.start AS occurrence,[term.canonical_id] AS via
      }
      WITH source,target,kind,occurrence,via
      WHERE occurrence >= $start AND occurrence < $end AND target.snapshot_id=$sid
        AND target.document_version_id IN $documents AND target.agreement_id=source.agreement_id
      RETURN DISTINCT source.canonical_id AS source, target.canonical_id AS target,kind,via
      ORDER BY kind,target LIMIT 13'''
    with graph.driver(settings) as conn,conn.session() as session:
        for seed in seeds:
            if deadline is not None and time.monotonic()>=deadline:limited=True;break
            values=session.run(graph.Query(query,timeout=2),sid=sid,doc=seed['document_version_id'],start=seed['start'],end=seed['end'],documents=list(document_ids)).data()
            if len(values)>12:limited=True
            for path in values:
                if path['target'] not in seen:
                    if len(targets)>=12:limited=True;continue
                    seen.add(path['target']);targets.append(path['target']);paths.append(path)
            rows=session.run('''MATCH (s:KBNode {snapshot_id:$sid})-[:HAS_UNRESOLVED_REFERENCE]->(u:UnresolvedReference)
                WHERE s.document_version_id=$doc AND s.start<$end AND s.end>$start AND u.start>=$start AND u.start<$end
                RETURN DISTINCT u.canonical_id AS reference_id,u.literal_reference AS literal,u.resolution_status AS status LIMIT 30''',
                sid=sid,doc=seed['document_version_id'],start=seed['start'],end=seed['end']).data()
            gaps.extend(rows)
    return targets,paths,list({g['reference_id']:g for g in gaps}.values()),limited
