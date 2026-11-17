"""Authenticated HTTP/SSE access to durable runs; no provider calls in HTTP."""
import html
import json
import time
import uuid
from typing import Annotated,Literal
from fastapi import Depends,Header,Query
from fastapi.responses import JSONResponse,PlainTextResponse,StreamingResponse
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from accordtrace_backend.common.core import DomainError
from accordtrace_backend.chat.models import Conversation, Event, Message, ModelCall, Run, ToolCall
from accordtrace_backend.chat.prompts import configuration
from accordtrace_backend.chat.repository import TERMINAL, authorize, authorize_run, cancel, create_conversation, run_view, submit
from accordtrace_backend.chat.schemas import CreateConversation, MessageRequest


def conversation_view(row):
    return dict(id=row.id,snapshot_id=row.snapshot_id,agreement_ids=row.agreements,
        document_version_ids=row.documents,created_at=row.created_at)


def install(app,settings,engine,auth):
    prefix='/api/v1'
    @app.post(prefix+'/conversations',status_code=201)
    def create(body:CreateConversation,owner=Depends(auth)):
        return conversation_view(create_conversation(engine,owner,body,settings=settings))
    @app.get(prefix+'/conversations')
    def conversations(owner=Depends(auth),offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
        with Session(engine) as db:
            rows=db.scalars(select(Conversation).where(Conversation.owner_id==owner).order_by(Conversation.created_at,Conversation.id).offset(offset).limit(limit))
            items=[]
            for row in rows:
                try:authorize(db,row.id,owner)
                except DomainError:continue
                items.append(conversation_view(row))
            return dict(items=items,offset=offset,limit=limit)
    @app.get(prefix+'/conversations/{conversation_id}')
    def conversation(conversation_id:uuid.UUID,owner=Depends(auth)):
        with Session(engine) as db:return conversation_view(authorize(db,str(conversation_id),owner))
    @app.get(prefix+'/conversations/{conversation_id}/messages')
    def messages(conversation_id:uuid.UUID,owner=Depends(auth),after:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
        with Session(engine) as db:
            authorize(db,str(conversation_id),owner)
            rows=db.scalars(select(Message).where(Message.conversation_id==str(conversation_id),Message.position>after).order_by(Message.position).limit(limit))
            return dict(items=[dict(id=r.id,position=r.position,role=r.role,content=r.content,run_id=r.run_id,result=r.result) for r in rows])
    @app.post(prefix+'/conversations/{conversation_id}/messages',status_code=202)
    def message(conversation_id:uuid.UUID,body:MessageRequest,
        idempotency_key:Annotated[str,Header(min_length=1,max_length=160)],owner=Depends(auth)):
        return run_view(submit(settings,engine,owner,str(conversation_id),body,idempotency_key,configuration(settings)))
    @app.get(prefix+'/runs/{run_id}')
    def run(run_id:uuid.UUID,owner=Depends(auth)):
        with Session(engine) as db:return run_view(authorize_run(db,str(run_id),owner))
    @app.post(prefix+'/runs/{run_id}/cancel')
    def cancellation(run_id:uuid.UUID,owner=Depends(auth)):return run_view(cancel(engine,owner,str(run_id)))
    @app.get(prefix+'/runs/{run_id}/trace')
    def trace(run_id:uuid.UUID,owner=Depends(auth)):
        with Session(engine) as db:
            row=authorize_run(db,str(run_id),owner)
            models=db.scalars(select(ModelCall).where(ModelCall.run_id==row.id).order_by(ModelCall.created_at))
            tools=db.scalars(select(ToolCall).where(ToolCall.run_id==row.id).order_by(ToolCall.id))
            return dict(run=run_view(row),configuration=row.configuration,
                models=[dict(step=m.step,attempt=m.attempt,provider=m.provider,model=m.model,status=m.status,error_code=m.error_code,usage=m.usage) for m in models],
                tools=[dict(step=t.step,kind=t.kind,status=t.status,evidence_ids=t.detail.get('evidence_ids',[])) for t in tools])
    @app.get(prefix+'/runs/{run_id}/export')
    def export(run_id:uuid.UUID,format:Literal['json','markdown']='json',owner=Depends(auth)):
        with Session(engine) as db:
            row=authorize_run(db,str(run_id),owner)
            if row.status!='completed':raise DomainError('run_not_completed')
            result={**row.result,'run_id':row.id}
            if format=='json':return JSONResponse(result)
            # Escape active HTML and Markdown controls in untrusted text.
            def escape(value):
                value=html.escape(value)
                for char in ('\\','`','*','_','[',']','<','>','#'):value=value.replace(char,'\\'+char)
                return value
            lines=['AccordTrace result',escape(result['status']),escape(result['answer'])]
            for claim in result.get('claims',[]):
                for citation in claim['citations']:
                    lines.append(escape(citation['evidence_id']+': '+citation['agreement_id']+' '+citation['quote']))
                    lines.append(escape('Unicode offsets '+str(citation['start'])+':'+str(citation['end'])+'; source hash '+citation['source_hash']))
            lines.extend(escape(g) for g in result.get('gaps',[]))
            for finding in result.get('review_findings',[]):lines.append(escape('Reviewer: '+finding['claim_id']+' '+finding['verdict']+' - '+finding['reason']))
            for item in result.get('reviewed_evidence',[]):lines.append(escape(item['evidence_id']+': '+item['quote']))
            return PlainTextResponse('\n\n'.join(lines),media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="accordtrace-result.md"'})
    @app.get(prefix+'/runs/{run_id}/events')
    def events(run_id:uuid.UUID,last_event_id:Annotated[str|None,Header()]=None,owner=Depends(auth)):
        try:cursor=int(last_event_id or 0)
        except ValueError:raise DomainError('invalid_event_cursor',422) from None
        with Session(engine) as db:
            row=authorize_run(db,str(run_id),owner)
            if cursor<0 or cursor>row.event_sequence:raise DomainError('invalid_event_cursor',422)
            first=db.scalar(select(func.min(Event.sequence)).where(Event.run_id==row.id))
            if (first and cursor<first-1) or (first is None and row.event_sequence and cursor<row.event_sequence):
                raise DomainError('event_history_expired',410)
        def stream():
            nonlocal cursor
            last_keepalive=time.monotonic()
            while True:
                with Session(engine) as db:
                    try:row=authorize_run(db,str(run_id),owner)
                    except DomainError:
                        yield 'event: access_revoked\ndata: {}\n\n';return
                    rows=list(db.scalars(select(Event).where(Event.run_id==row.id,Event.sequence>cursor).order_by(Event.sequence).limit(100)))
                    terminal=row.status in TERMINAL
                    # Read and access check share the same transaction; no long-held connection.
                    payloads=[(r.sequence,r.kind,json.dumps(r.payload,ensure_ascii=True)) for r in rows]
                for sequence,kind,payload in payloads:
                    cursor=sequence;yield 'id: '+str(sequence)+'\nevent: '+kind+'\ndata: '+payload+'\n\n'
                if terminal and len(rows)<100:return
                if time.monotonic()-last_keepalive>10:
                    yield ': keepalive\n\n';last_keepalive=time.monotonic()
                time.sleep(0.3)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
