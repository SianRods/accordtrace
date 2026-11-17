"""Transactional chat authority: access, budgets, ownership and one final answer."""
import uuid
from datetime import timedelta

from sqlalchemy import and_,func,or_,select,text
from sqlalchemy.orm import Session

from accordtrace_backend.indexing.chunking import fingerprint
from accordtrace_backend.common.core import DomainError
from accordtrace_backend.indexing.pipeline import pin, snapshot_documents
from accordtrace_backend.retrieval.search import allowed_documents
from accordtrace_backend.persistence.storage import utcnow
from accordtrace_backend.chat.models import Artifact, Conversation, Event, Message, ModelCall, Run

TERMINAL=('completed','failed','cancelled')
LIMITS=dict(generation=5,retrieval=6,correction=2,revision=1,schema_repair=1,graph_units=12,embedding=6)


class Halt(DomainError):pass
class Cancelled(Halt):pass
class QuotaWait(Exception):
    def __init__(self,retry_at):self.retry_at=retry_at


def authorize(db,conversation_id,owner):
    conversation=db.get(Conversation,conversation_id)
    if conversation is None or conversation.owner_id!=owner:raise DomainError('conversation_not_found',404)
    permitted={d.id for d in allowed_documents(db,conversation.agreements)}
    if not set(conversation.documents)<=permitted:raise DomainError('source_access_revoked',403)
    return conversation


def authorize_run(db,run_id,owner):
    run=db.get(Run,run_id)
    if run is None:raise DomainError('run_not_found',404)
    authorize(db,run.conversation_id,owner)
    return run


def event(db,run,key,kind,payload):
    if db.scalar(select(Event.sequence).where(Event.run_id==run.id,Event.key==key)) is not None:return
    run.event_sequence+=1
    db.add(Event(run_id=run.id,sequence=run.event_sequence,key=key,kind=kind,payload=payload))


def create_conversation(db_engine,owner,body,allow_mock=False):
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        snapshot=pin(db,body.snapshot_id)
        if snapshot.configuration['embedding']['verification']!='live' and not allow_mock:
            raise DomainError('mock_snapshot_not_for_chat')
        docs=[d for d in snapshot_documents(db,snapshot.id) if d.agreement_id in body.agreement_ids]
        permitted={d.id for d in allowed_documents(db,body.agreement_ids)}
        if set(body.agreement_ids)!={d.agreement_id for d in docs} or not {d.id for d in docs}<=permitted:
            raise DomainError('agreement_outside_snapshot_scope',403)
        row=Conversation(id=str(uuid.uuid4()),owner_id=owner,snapshot_id=snapshot.id,
            agreements=sorted(body.agreement_ids),documents=sorted(d.id for d in docs),next_position=0)
        db.add(row);db.flush();return row


def submit(settings,db_engine,owner,conversation_id,body,key,configuration):
    if not key or len(key)>160:raise DomainError('invalid_idempotency_key',422)
    request=body.model_dump();input_hash=fingerprint(request)
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        conversation=authorize(db,conversation_id,owner)
        db.refresh(conversation,with_for_update=True)
        old=db.scalar(select(Run).where(Run.conversation_id==conversation_id,Run.idempotency_key==key))
        if old:
            if old.input_hash!=input_hash:raise DomainError('idempotency_key_conflict')
            return old
        if db.scalar(select(Run.id).where(Run.conversation_id==conversation_id,Run.status.not_in(TERMINAL))):
            raise DomainError('conversation_busy')
        run_id=str(uuid.uuid4());message_id=str(uuid.uuid4())
        conversation.next_position+=1
        db.add(Message(id=message_id,conversation_id=conversation_id,position=conversation.next_position,
                       role='user',content=body.content,run_id=run_id))
        db.flush()
        run=Run(id=run_id,conversation_id=conversation_id,message_id=message_id,idempotency_key=key,
            input_hash=input_hash,request=request,configuration=configuration,status='queued',
            counters={k:0 for k in LIMITS},active_seconds=0,event_sequence=0,
            expires_at=utcnow()+timedelta(seconds=settings.chat_lifetime_seconds))
        db.add(run);db.flush();event(db,run,'queued','queued',{'run_id':run.id})
        return run


def claim(db_engine,lease_seconds,run_id=None):
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        query=select(Run).where(or_(Run.status=='queued',and_(Run.status=='running',Run.lease_until<utcnow()),
            and_(Run.status=='waiting_quota',Run.retry_at<=utcnow()),Run.status=='cancelling'))
        if run_id:query=query.where(Run.id==run_id)
        row=db.scalar(query.order_by(Run.created_at).with_for_update(skip_locked=True).limit(1))
        if row is None:return None
        if row.status=='cancelling' and row.lease_until and row.lease_until>utcnow():return None
        row.status='running';row.lease_token=str(uuid.uuid4());row.lease_until=utcnow()+timedelta(seconds=lease_seconds)
        row.attempts+=1;row.retry_at=None
        event(db,row,'claimed:'+str(row.attempts),'resumed' if row.attempts>1 else 'started',{'attempt':row.attempts})
        return row.id,row.lease_token


def owned(db,run_id,token,lease_seconds=120,allow_cancel=False):
    run=db.get(Run,run_id,with_for_update=True)
    if run is None or run.status not in ('running','cancelling') or run.lease_token!=token or not run.lease_until or run.lease_until<=utcnow():
        raise Halt('chat_lease_lost')
    run.lease_until=utcnow()+timedelta(seconds=lease_seconds)
    if not allow_cancel:
        if run.cancel_requested:raise Cancelled('cancelled')
        if run.expires_at<=utcnow():raise Halt('run_lifetime_exceeded')
        if run.active_seconds>=run.configuration['active_seconds']:raise Halt('active_budget_exceeded')
    conversation=db.get(Conversation,run.conversation_id)
    authorize(db,conversation.id,conversation.owner_id)
    return run


def reserve(run,counter,amount=1):
    values=dict(run.counters)
    if values[counter]+amount>LIMITS[counter]:raise Halt('budget_'+counter)
    values[counter]+=amount;run.counters=values


def get_artifact(db_engine,run_id,key,input_hash):
    with Session(db_engine) as db:
        row=db.get(Artifact,(run_id,key))
        if row:
            if row.input_hash!=input_hash:raise Halt('node_input_changed')
            return row.value
    return None


def put_artifact(db_engine,run_id,token,key,input_hash,value):
    with Session(db_engine) as db,db.begin():
        owned(db,run_id,token)
        db.merge(Artifact(run_id=run_id,key=key,input_hash=input_hash,value=value))


def cancel(db_engine,owner,run_id):
    with Session(db_engine,expire_on_commit=False) as db,db.begin():
        row=authorize_run(db,run_id,owner);db.refresh(row,with_for_update=True)
        if row.status in TERMINAL:return row
        row.cancel_requested=True
        if row.status in ('queued','waiting_quota'):
            row.status='cancelled';row.lease_token=None;row.lease_until=None
            event(db,row,'cancelled','cancelled',{'run_id':row.id})
        else:
            row.status='cancelling';event(db,row,'cancel_requested','cancel_requested',{'run_id':row.id})
        return row


def summary(db,conversation):
    rows=db.scalars(select(Message).where(Message.conversation_id==conversation.id,Message.role=='assistant')
                    .order_by(Message.position.desc()).limit(3)).all()
    values=[]
    for row in reversed(rows):
        run=db.get(Run,row.run_id);question=db.get(Message,run.message_id)
        values.append(dict(question=question.content[:600],claims=[dict(text=c['text'][:500],citations=c['citations'])
            for c in row.result.get('claims',[])][:4]))
    return values


def finalize(db_engine,run_id,token,result,claims,citations):
    from accordtrace_backend.chat.models import ClaimRecord, CitationRecord
    with Session(db_engine) as db,db.begin():
        run=owned(db,run_id,token,allow_cancel=True)
        if run.cancel_requested:
            run.status='cancelled';event(db,run,'cancelled','cancelled',{'run_id':run.id})
        else:
            conversation=db.get(Conversation,run.conversation_id,with_for_update=True)
            result=dict(result,run_id=run.id,scope=dict(snapshot_id=conversation.snapshot_id,
                agreement_ids=conversation.agreements,document_version_ids=conversation.documents))
            conversation.next_position+=1
            db.add(Message(id=str(uuid.uuid4()),conversation_id=conversation.id,position=conversation.next_position,
                role='assistant',content=result['answer'],run_id=run.id,result=result))
            for claim in claims:db.add(ClaimRecord(run_id=run.id,**claim))
            for citation in citations:db.add(CitationRecord(id=str(uuid.uuid4()),run_id=run.id,**citation))
            run.status='completed';run.answer_status=result['status'];run.result=result
            event(db,run,'final','final',result)
        run.stage=run.status;run.lease_token=None;run.lease_until=None


def run_view(run):
    return dict(id=run.id,conversation_id=run.conversation_id,message_id=run.message_id,status=run.status,
        answer_status=run.answer_status,stage=run.stage,counters=run.counters,active_seconds=run.active_seconds,
        retry_at=run.retry_at,result=run.result if run.status=='completed' else None,error_code=run.error_code)
