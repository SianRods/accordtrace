"""Application persistence; checkpoint tables have their own explicit setup."""
from datetime import datetime
from sqlalchemy import (JSON,Boolean,DateTime,Float,ForeignKey,Index,Integer,String,Text,UniqueConstraint,text)
from sqlalchemy.orm import Mapped,mapped_column
from accordtrace_backend.persistence.storage import Base, utcnow


class Conversation(Base):
    __tablename__='chat_conversation'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    owner_id:Mapped[str]=mapped_column(String(100),index=True)
    snapshot_id:Mapped[str]=mapped_column(ForeignKey('corpus_snapshot.id'))
    agreements:Mapped[list]=mapped_column(JSON)
    documents:Mapped[list]=mapped_column(JSON)
    next_position:Mapped[int]=mapped_column(Integer,default=0)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class Message(Base):
    __tablename__='chat_message'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    conversation_id:Mapped[str]=mapped_column(ForeignKey('chat_conversation.id'),index=True)
    position:Mapped[int]=mapped_column(Integer)
    role:Mapped[str]=mapped_column(String(20))
    content:Mapped[str]=mapped_column(Text)
    run_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('conversation_id','position'),UniqueConstraint('run_id','role'))


class Run(Base):
    __tablename__='chat_run'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    conversation_id:Mapped[str]=mapped_column(ForeignKey('chat_conversation.id'),index=True)
    message_id:Mapped[str]=mapped_column(ForeignKey('chat_message.id'),unique=True)
    idempotency_key:Mapped[str]=mapped_column(String(160))
    input_hash:Mapped[str]=mapped_column(String(64))
    request:Mapped[dict]=mapped_column(JSON)
    configuration:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String(30),default='queued',index=True)
    answer_status:Mapped[str|None]=mapped_column(String(40),nullable=True)
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    stage:Mapped[str]=mapped_column(String(40),default='queued')
    counters:Mapped[dict]=mapped_column(JSON,default=dict)
    active_seconds:Mapped[float]=mapped_column(Float,default=0)
    lease_token:Mapped[str|None]=mapped_column(String(36),nullable=True)
    lease_until:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    retry_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    cancel_requested:Mapped[bool]=mapped_column(Boolean,default=False)
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    event_sequence:Mapped[int]=mapped_column(Integer,default=0)
    error_code:Mapped[str|None]=mapped_column(String(80),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    __table_args__=(UniqueConstraint('conversation_id','idempotency_key'),
        Index('uq_chat_conversation_active','conversation_id',unique=True,
              postgresql_where=text("status IN ('queued','running','waiting_quota','cancelling')")))


class RunEvidence(Base):
    __tablename__='chat_evidence'
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),primary_key=True)
    label:Mapped[str]=mapped_column(String(30),primary_key=True)
    chunk_id:Mapped[str|None]=mapped_column(ForeignKey('child_chunk.id'),nullable=True)
    span_id:Mapped[str]=mapped_column(ForeignKey('evidence_span.id'))
    document_id:Mapped[str]=mapped_column(ForeignKey('document_version.id'))
    start:Mapped[int]=mapped_column(Integer)
    end:Mapped[int]=mapped_column(Integer)
    text_hash:Mapped[str]=mapped_column(String(64))
    source_hash:Mapped[str]=mapped_column(String(64))
    selected:Mapped[bool]=mapped_column(Boolean,default=True)
    metadata_:Mapped[dict]=mapped_column('metadata',JSON,default=dict)


class ClaimRecord(Base):
    __tablename__='chat_claim'
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),primary_key=True)
    id:Mapped[str]=mapped_column(String(40),primary_key=True)
    text:Mapped[str]=mapped_column(Text)
    verdict:Mapped[str]=mapped_column(String(30))


class CitationRecord(Base):
    __tablename__='chat_citation'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),index=True)
    claim_id:Mapped[str]=mapped_column(String(40))
    evidence_label:Mapped[str]=mapped_column(String(30))
    detail:Mapped[dict]=mapped_column(JSON)


class Event(Base):
    __tablename__='chat_event'
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),primary_key=True)
    sequence:Mapped[int]=mapped_column(Integer,primary_key=True)
    key:Mapped[str]=mapped_column(String(180))
    kind:Mapped[str]=mapped_column(String(40))
    payload:Mapped[dict]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('run_id','key'),)


class Artifact(Base):
    __tablename__='chat_artifact'
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),primary_key=True)
    key:Mapped[str]=mapped_column(String(120),primary_key=True)
    input_hash:Mapped[str]=mapped_column(String(64))
    value:Mapped[dict]=mapped_column(JSON)


class ModelCall(Base):
    __tablename__='chat_model_call'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),index=True)
    step:Mapped[str]=mapped_column(String(100))
    attempt:Mapped[int]=mapped_column(Integer)
    provider:Mapped[str]=mapped_column(String(20))
    model:Mapped[str]=mapped_column(String(100))
    input_hash:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(30))
    error_code:Mapped[str|None]=mapped_column(String(80),nullable=True)
    usage:Mapped[dict]=mapped_column(JSON,default=dict)
    timeout:Mapped[float]=mapped_column(Float)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('run_id','step','attempt'),)


class ToolCall(Base):
    __tablename__='chat_tool_call'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    run_id:Mapped[str]=mapped_column(ForeignKey('chat_run.id'),index=True)
    step:Mapped[str]=mapped_column(String(100))
    input_hash:Mapped[str]=mapped_column(String(64))
    kind:Mapped[str]=mapped_column(String(30))
    status:Mapped[str]=mapped_column(String(30))
    detail:Mapped[dict]=mapped_column(JSON,default=dict)


class ProviderWindow(Base):
    __tablename__='chat_provider_window'
    id:Mapped[str]=mapped_column(String(130),primary_key=True)
    minute_start:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    day_start:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    requests:Mapped[int]=mapped_column(Integer,default=0)
    tokens:Mapped[int]=mapped_column(Integer,default=0)
    day_requests:Mapped[int]=mapped_column(Integer,default=0)
    day_tokens:Mapped[int]=mapped_column(Integer,default=0)
    failures:Mapped[int]=mapped_column(Integer,default=0)
    blocked_until:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
