"""Bounded LangGraph workflow. Checkpoints contain stage references, not prompts."""
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from sqlalchemy import select
from sqlalchemy.orm import Session
from accordtrace_backend.indexing.chunking import fingerprint
from accordtrace_backend.common.core import DomainError
from accordtrace_backend.persistence.storage import Document
from accordtrace_backend.chat.evidence import Tools, verify_claims
from accordtrace_backend.chat.models import Artifact, Conversation, Run
from accordtrace_backend.chat.providers import Gateway, configured
from accordtrace_backend.chat.repository import Halt, event, owned, put_artifact, reserve, summary, finalize
from accordtrace_backend.chat.schemas import Action, Citation, Claim, Draft, Plan, Review


class State(TypedDict,total=False):
    run_id:str
    route:str
    revision:bool
    clarification:bool
    empty:bool


def action(kind,**values):
    return Action(kind=kind,query=values.get('query'),section_label=values.get('section_label'),
        term=values.get('term'),evidence_id=values.get('evidence_id'))


class Workflow:
    def __init__(self,settings,engine,run_id,token,providers,embedding_provider=None):
        self.settings,self.engine,self.run_id,self.token=settings,engine,run_id,token
        self.gateway=Gateway(settings,engine,run_id,token,providers)
        self.providers=providers
        self.tools=Tools(settings,engine,run_id,token,embedding_provider)
        with Session(engine) as db,db.begin():
            run=owned(db,run_id,token);convo=db.get(Conversation,run.conversation_id)
            self.request=run.request;self.scope=dict(snapshot_id=convo.snapshot_id,agreement_ids=convo.agreements)
            self.history=summary(db,convo)
            # Previous prose is intent only; previous evidence is rehydrated and checked.
            for turn in self.history:
                for claim in turn['claims']:
                    validated=[]
                    for citation in claim['citations']:
                        doc=db.get(Document,citation['document_version_id'])
                        if doc and doc.id in convo.documents and doc.text[citation['start']:citation['end']]==citation['quote']:
                            validated.append(dict(document_version_id=doc.id,start=citation['start'],end=citation['end']))
                    claim['citations']=validated
    def save(self,key,value):
        put_artifact(self.engine,self.run_id,self.token,'workflow:'+key,fingerprint(key),value)
    def load(self,key):
        with Session(self.engine) as db:
            row=db.get(Artifact,(self.run_id,'workflow:'+key))
            if row is None:raise Halt('workflow_artifact_missing')
            return row.value
    def stage(self,name):
        with Session(self.engine) as db,db.begin():
            run=owned(db,self.run_id,self.token);run.stage=name
            event(db,run,'stage:'+name,'stage',{'stage':name})
    def route(self,state):
        self.stage('route')
        if self.request['mode']=='extractive':return dict(route='extractive')
        if not self.providers.scripted and not configured(self.settings):raise Halt('chat_credentials_not_configured')
        return dict(route='plan')
    def plan(self,state):
        self.stage('plan')
        plan=self.gateway.generate('planner','plan',dict(question=self.request['content'],history=self.history,
            scope=self.scope,max_tasks=3),Plan)
        if not plan.clarification and not plan.tasks:raise Halt('empty_retrieval_plan')
        self.save('plan',plan.model_dump());return dict(clarification=bool(plan.clarification))
    def investigate(self,state):
        self.stage('investigate');plan=Plan.model_validate(self.load('plan'));packets=[]
        for index,task in enumerate(plan.tasks):
            packets.append(self.tools.execute(task.action,'task:'+str(index)))
        self.save('packets',{'items':packets});return {}
    def correct(self,state):
        self.stage('structural_correction');packets=self.load('packets')['items']
        if not self.tools.context():
            query=Plan.model_validate(self.load('plan')).standalone_question
            while len(query.encode())>1536:query=query[:-1]
            packet=self.tools.execute(action('search',query=query),'empty_repair',True)
            if packet not in packets:packets.append(packet)
            self.save('packets',{'items':packets})
            return dict(empty=not bool(self.tools.context()))
        # Deterministic correction addresses visible unresolved section dependencies.
        import re
        for packet in packets:
            for gap in packet.get('gaps',[]):
                match=re.search(r'\b(\d{1,2}\.\d{1,2})\b',gap.get('literal',''))
                if match and gap.get('status')=='not_expanded':
                    packets.append(self.tools.execute(action('lookup_section',section_label=match[1]),'structural',True))
                    self.save('packets',{'items':packets});return {}
        return {}
    def no_evidence(self,state):
        self.save('draft:initial',Draft(claims=[],gaps=['No source evidence was found within the retrieval budget.']).model_dump())
        self.save('review:initial',Review(verdicts=[],material_gaps=[],repair=None).model_dump())
        return {}
    def payload(self):
        return dict(question=Plan.model_validate(self.load('plan')).standalone_question,scope=self.scope,
            evidence=self.tools.context(),retrieval=self.load('packets'),history=self.history)
    def draft(self,state):
        self.stage('draft');value=self.gateway.generate('draft','draft',self.payload(),Draft)
        self.save('draft:initial',value.model_dump());return dict(revision=False)
    def probe(self,state):
        self.stage('counterevidence')
        query=Plan.model_validate(self.load('plan')).standalone_question
        # Independent retrieval is not chosen by the drafting model.
        query=(query[:850]+' exceptions exclusions limitations notwithstanding termination conditions')
        while len(query.encode())>1536:query=query[:-1]
        packet=self.tools.execute(action('search',query=query),'counterevidence')
        packets=self.load('packets')['items'];
        if packet not in packets:packets.append(packet)
        self.save('packets',{'items':packets})
        return {}
    def review(self,state):
        suffix='revision' if state.get('revision') else 'initial';self.stage('review_'+suffix)
        value=self.gateway.generate('reviewer','review:'+suffix,dict(**self.payload(),draft=self.load('draft:revision' if state.get('revision') else 'draft:initial')),Review)
        ids={c.id for c in Draft.model_validate(self.load('draft:revision' if state.get('revision') else 'draft:initial')).claims}
        if len({v.claim_id for v in value.verdicts})!=len(value.verdicts) or {v.claim_id for v in value.verdicts}!=ids:
            raise Halt('invalid_review_claim_ids')
        self.save('review:'+suffix,value.model_dump());return {}
    def needs_revision(self,state):
        review=Review.model_validate(self.load('review:initial'))
        if state.get('revision') or review.repair is None:return 'finish'
        with Session(self.engine) as db:
            run=db.get(Run,self.run_id)
            # A revision must leave room for an independent re-review.
            if run.counters['generation']>3 or run.counters['retrieval']>=6 or run.counters['correction']>=2:return 'finish'
            from accordtrace_backend.chat.models import ToolCall
            if db.scalar(select(ToolCall.id).where(ToolCall.run_id==run.id,
                ToolCall.input_hash==fingerprint(review.repair.model_dump()),ToolCall.status=='succeeded')):return 'finish'
        return 'revise' if review.material_gaps or any(v.verdict!='supported' for v in review.verdicts) else 'finish'
    def revise(self,state):
        self.stage('revision')
        with Session(self.engine) as db,db.begin():
            run=owned(db,self.run_id,self.token)
            # Idempotent node replay does not reserve the revision twice.
            if not db.get(Artifact,(run.id,'revision_reserved')):
                reserve(run,'revision');db.add(Artifact(run_id=run.id,key='revision_reserved',input_hash='revision',value={}))
        review=Review.model_validate(self.load('review:initial'))
        packet=self.tools.execute(review.repair,'review_repair',True)
        packets=self.load('packets')['items'];
        if packet not in packets:packets.append(packet)
        self.save('packets',{'items':packets})
        payload=dict(**self.payload(),previous_draft=self.load('draft:initial'),review=self.load('review:initial'))
        value=self.gateway.generate('draft','revision',payload,Draft);self.save('draft:revision',value.model_dump())
        return dict(revision=True)
    def extractive(self,state):
        self.stage('extractive')
        selector='lookup_section' if self.request.get('section_label') else 'lookup_term'
        packet=self.tools.execute(action(selector,section_label=self.request.get('section_label'),term=self.request.get('defined_term')),'extractive')
        draft=Draft(claims=[Claim(id='C'+str(i+1),text=e['quote'],citations=[Citation(evidence_id=e['evidence_id'],quote=e['quote'])])
            for i,e in enumerate(self.tools.context()[:8])],gaps=['Verbatim excerpts only; no legal interpretation.'])
        self.save('packets',{'items':[packet]});self.save('draft:initial',draft.model_dump());return {}
    def finish(self,state):
        self.stage('verify')
        if state.get('clarification'):
            result=dict(status='needs_clarification',answer=self.load('plan')['clarification'],claims=[],gaps=[],scope=self.scope)
            claims=[];citations=[]
        else:
            draft=Draft.model_validate(self.load('draft:revision' if state.get('revision') else 'draft:initial'));extractive=state.get('route')=='extractive'
            review=None if extractive else Review.model_validate(self.load('review:revision' if state.get('revision') else 'review:initial'))
            claims,citations,missing,conflict=verify_claims(self.engine,self.run_id,self.token,draft,review,extractive)
            gaps=list(draft.gaps)+missing+(review.material_gaps if review else [])
            with Session(self.engine) as db:
                if db.scalar(select(Artifact.key).where(Artifact.run_id==self.run_id,Artifact.key.like('context:%')).limit(1)):
                    gaps.append('Some context was omitted to respect the configured provider token budget.')
            packets=self.load('packets')['items']
            for packet in packets:
                gaps.extend('Unresolved dependency: '+g.get('literal','')+' ('+g.get('status','unknown')+')' for g in packet.get('gaps',[]))
                gaps.extend('Retrieval limitation: '+(w if isinstance(w,str) else w.get('code','unknown')) for w in packet.get('warnings',[]))
            status='conflicting_evidence' if conflict else ('partially_supported' if gaps and claims and not extractive else 'supported') if claims else 'insufficient_evidence'
            answer='\n\n'.join(c['text']+' '+''.join('['+v['evidence_id']+']' for v in c['citations']) for c in claims)
            if not answer:answer='The available evidence does not support a verified answer.'
            result=dict(status=status,answer=answer,claims=claims,gaps=list(dict.fromkeys(gaps)),scope=self.scope,
                review_findings=[v.model_dump() for v in review.verdicts] if review else [],
                reviewed_evidence=self.tools.context() if conflict else [])
        finalize(self.engine,self.run_id,self.token,result,[{k:c[k] for k in ('id','text','verdict')} for c in claims],citations)
        return {}
    def graph(self,saver):
        builder=StateGraph(State)
        for name in ('route','plan','investigate','correct','no_evidence','draft','probe','review','revise','extractive','finish'):
            builder.add_node(name,getattr(self,name))
        builder.add_edge(START,'route')
        builder.add_conditional_edges('route',lambda s:s['route'],{'plan':'plan','extractive':'extractive'})
        builder.add_conditional_edges('plan',lambda s:s['clarification'],{True:'finish',False:'investigate'})
        builder.add_conditional_edges('correct',lambda s:bool(s.get('empty')),{True:'no_evidence',False:'draft'})
        for a,b in [('investigate','correct'),('no_evidence','finish'),('draft','probe'),('probe','review'),('revise','review'),('extractive','finish')]:builder.add_edge(a,b)
        builder.add_conditional_edges('review',self.needs_revision,{'revise':'revise','finish':'finish'})
        builder.add_edge('finish',END)
        return builder.compile(checkpointer=saver)
