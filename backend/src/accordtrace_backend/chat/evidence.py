"""Run-scoped tools and canonical citation checks. Model labels never grant access."""
import time
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from accordtrace_backend.indexing import embedding
from accordtrace_backend.retrieval import search as retrieval
from accordtrace_backend.indexing.chunking import fingerprint
from accordtrace_backend.common.core import DomainError, digest
from accordtrace_backend.persistence.storage import Chunk, ChunkMembership, Document, Snapshot, Span
from accordtrace_backend.chat.models import Conversation, Run, RunEvidence, ToolCall, Artifact
from accordtrace_backend.chat.repository import Halt, owned, reserve, get_artifact, put_artifact


class Budget:
    def __init__(self,engine,run_id,token):self.engine,self.run_id,self.token=engine,run_id,token
    def check(self):
        with Session(self.engine) as db,db.begin():owned(db,self.run_id,self.token)
    def graph_unit(self,target):
        with Session(self.engine) as db,db.begin():
            run=owned(db,self.run_id,self.token)
            key='graph:'+target
            if db.get(Artifact,(run.id,key)):return True
            if run.counters['graph_units']>=12:return False
            reserve(run,'graph_units');db.add(Artifact(run_id=run.id,key=key,input_hash=target,value={}))
            return True
    def embedding_provider(self,settings,profile):
        provider=embedding.provider_for(settings,profile)
        def before_call():
            with Session(self.engine) as db,db.begin():
                run=owned(db,self.run_id,self.token);reserve(run,'embedding')
                provider.query_timeout_seconds=min(5,run.configuration['active_seconds']-run.active_seconds)
        provider.before_call=before_call
        return provider


def hydrate(db,conversation,row):
    doc=db.get(Document,row.document_id);span=db.get(Span,row.span_id)
    if doc is None or span is None or doc.id not in conversation.documents or doc.agreement_id not in conversation.agreements:
        raise Halt('citation_scope_violation')
    if row.chunk_id and db.get(ChunkMembership,(conversation.snapshot_id,row.chunk_id)) is None:raise Halt('citation_membership_violation')
    quote=doc.text[row.start:row.end]
    if not span.start<=row.start<row.end<=span.end or digest(quote.encode())!=row.text_hash or digest(doc.text.encode())!=doc.text_hash or doc.raw_hash!=row.source_hash:
        raise Halt('citation_integrity_failed')
    return dict(evidence_id=row.label,document_version_id=doc.id,agreement_id=doc.agreement_id,span_id=span.id,
        chunk_id=row.chunk_id,start=row.start,end=row.end,quote=quote,text_hash=row.text_hash,source_hash=row.source_hash,
        section_label=span.label,offset_unit='unicode_codepoints',metadata=row.metadata_)


class Tools:
    def __init__(self,settings,engine,run_id,token,embedding_provider=None):
        self.settings,self.engine,self.run_id,self.token=settings,engine,run_id,token
        self.budget=Budget(engine,run_id,token)
        self.provider=embedding_provider
    def context(self):
        with Session(self.engine) as db,db.begin():
            run=owned(db,self.run_id,self.token);convo=db.get(Conversation,run.conversation_id)
            return [hydrate(db,convo,row) for row in db.scalars(select(RunEvidence).where(
                RunEvidence.run_id==run.id,RunEvidence.selected.is_(True)).order_by(RunEvidence.label))]
    def execute(self,action,step,correction=False):
        value=action.model_dump();ih=fingerprint(value);key='tool:'+step
        cached=get_artifact(self.engine,self.run_id,key,ih)
        if cached is not None:return cached
        with Session(self.engine) as db,db.begin():
            run=owned(db,self.run_id,self.token);convo=db.get(Conversation,run.conversation_id)
            # A repeated identical request cannot manufacture fresh progress.
            previous=db.scalar(select(ToolCall).where(ToolCall.run_id==run.id,ToolCall.input_hash==ih,ToolCall.status=='succeeded'))
            if previous:return dict(evidence_ids=previous.detail.get('evidence_ids',[]),warnings=['duplicate_action'],gaps=[])
            reserve(run,'retrieval')
            if correction:reserve(run,'correction')
            call=ToolCall(id=str(uuid.uuid4()),run_id=run.id,step=step,input_hash=ih,kind=action.kind,status='in_flight')
            db.add(call);call_id=call.id
            sid=convo.snapshot_id;agreements=list(convo.agreements);documents=set(convo.documents)
            selected=None
            if action.kind in ('expand_parent','expand_graph'):
                row=db.get(RunEvidence,(run.id,action.evidence_id or ''))
                if row is None:raise Halt('unknown_evidence_label')
                selected=hydrate(db,convo,row)
        candidates=[];warnings=[];gaps=[]
        try:
            if action.kind in ('search','lookup_section','lookup_term'):
                request=retrieval.SearchRequest(query=(action.query or '')[:1536],snapshot_id=sid,agreement_ids=agreements,
                    mode='auto' if action.kind=='search' else 'exact',
                    section_label=action.section_label if action.kind=='lookup_section' else None,
                    defined_term=action.term if action.kind=='lookup_term' else None)
                packet=retrieval.search(self.settings,self.engine,request,self.provider,self.budget)
                candidates=[e for group in packet['groups'] for e in group['evidence']]
                warnings=packet['warnings']+packet['limits_reached'];gaps=packet['unresolved_dependencies']
            elif action.kind=='expand_parent':
                with Session(self.engine) as db:
                    span=db.get(Span,selected['span_id']);doc=db.get(Document,span.document_id)
                    candidates=[dict(parent_span_id=span.id,document_version_id=doc.id,chunk_id=None,start=span.start,end=span.end,
                        text_hash=span.text_hash,source_hash=doc.raw_hash,quote=doc.text[span.start:span.end])]
            else:
                seed=dict(selected,document_version_id=selected['document_version_id'])
                targets,paths,gaps,limited=retrieval.expand_graph(self.settings,sid,[seed],documents,time.monotonic()+5,self.budget)
                with Session(self.engine) as db:
                    for target in targets:
                        for chunk in db.scalars(select(Chunk).join(ChunkMembership,ChunkMembership.chunk_id==Chunk.id).where(
                            ChunkMembership.snapshot_id==sid,Chunk.parent_id==target,Chunk.document_id.in_(documents)).order_by(Chunk.start).limit(1)):
                            candidates.append(retrieval.evidence(db,chunk))
                if limited:warnings.append('graph_unit_budget')
            labels=self.select(candidates)
            result=dict(evidence_ids=labels,warnings=warnings,gaps=gaps)
            with Session(self.engine) as db,db.begin():
                owned(db,self.run_id,self.token);call=db.get(ToolCall,call_id);call.status='succeeded';call.detail=result
            put_artifact(self.engine,self.run_id,self.token,key,ih,result)
            return result
        except Exception:
            with Session(self.engine) as db,db.begin():
                call=db.get(ToolCall,call_id);call.status='failed';call.detail={'code':'retrieval_failed'}
            raise
    def select(self,candidates):
        with Session(self.engine) as db,db.begin():
            run=owned(db,self.run_id,self.token);convo=db.get(Conversation,run.conversation_id)
            snapshot=db.get(Snapshot,convo.snapshot_id)
            _,tokenizer=embedding.assets(self.settings,snapshot.configuration['assets'])
            existing=list(db.scalars(select(RunEvidence).where(RunEvidence.run_id==run.id).order_by(RunEvidence.label)))
            draft=db.get(Artifact,(run.id,'workflow:draft:revision')) or db.get(Artifact,(run.id,'workflow:draft:initial'))
            protected={c['evidence_id'] for claim in draft.value.get('claims',[]) for c in claim.get('citations',[])} if draft else set()
            # Retain prior context; additions replace oldest excerpts only when necessary.
            selected=[r for r in existing if r.selected];labels=[]
            for item in candidates:
                found=next((r for r in existing if (r.document_id,r.start,r.end)==(item['document_version_id'],item['start'],item['end'])),None)
                if found is None:
                    found=RunEvidence(run_id=run.id,label='E'+str(len(existing)+1),chunk_id=item.get('chunk_id'),
                        span_id=item['parent_span_id'],document_id=item['document_version_id'],start=item['start'],end=item['end'],
                        text_hash=item['text_hash'],source_hash=item['source_hash'],selected=False,
                        metadata_={'reason':item.get('reason','expansion')})
                    db.add(found);existing.append(found)
                detail=hydrate(db,convo,found)
                count=len(tokenizer.encode(detail['quote'],add_special_tokens=False).ids)
                if count>3000:continue
                if found not in selected:selected.append(found)
                found.selected=True;labels.append(found.label)
                def size(row):return len(tokenizer.encode(hydrate(db,convo,row)['quote'],add_special_tokens=False).ids)
                while sum(size(r) for r in selected)>3000:
                    index=next((i for i,r in enumerate(selected) if r.label not in protected),None)
                    if index is None:raise Halt('protected_evidence_context_budget')
                    removed=selected.pop(index);removed.selected=False
            return [label for label in labels if any(r.label==label for r in selected)]


def verify_claims(engine,run_id,token,draft,review,extractive=False):
    """Exact text/range validation is separate from the reviewer's semantic verdict."""
    claims=[];citations=[];missing=[];conflict=False
    verdicts={v.claim_id:v.verdict for v in review.verdicts} if review else {}
    if len({c.id for c in draft.claims})!=len(draft.claims):raise Halt('duplicate_claim_id')
    with Session(engine) as db,db.begin():
        run=owned(db,run_id,token);convo=db.get(Conversation,run.conversation_id)
        for claim in draft.claims:
            verdict='supported' if extractive else verdicts.get(claim.id,'unsupported')
            valid=[]
            for citation in claim.citations:
                row=db.get(RunEvidence,(run.id,citation.evidence_id))
                if row is None or not row.selected:continue
                detail=hydrate(db,convo,row);quote=citation.quote or detail['quote']
                if not quote or detail['quote'].count(quote)!=1:continue
                offset=detail['quote'].index(quote);detail.update(start=detail['start']+offset,end=detail['start']+offset+len(quote),quote=quote,text_hash=digest(quote.encode()))
                valid.append(detail)
            if verdict=='conflicting' and valid:conflict=True
            if verdict!='supported' or not valid or (extractive and any(claim.text!=v['quote'] for v in valid)):
                missing.append('Claim '+claim.id+' could not be supported by verified citations.');continue
            claims.append(dict(id=claim.id,text=claim.text,verdict=verdict,citations=valid))
            citations.extend(dict(claim_id=claim.id,evidence_label=v['evidence_id'],detail=v) for v in valid)
    return claims,citations,missing,conflict
