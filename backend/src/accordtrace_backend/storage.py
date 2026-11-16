"""Canonical relational schema and engine factory."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey,
                        Integer, String, Text, UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .core import Settings


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = 'document_version'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agreement_id: Mapped[str] = mapped_column(String(80), index=True)
    raw_hash: Mapped[str] = mapped_column(String(64))
    text_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    source_uri: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(String(80))
    byte_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('agreement_id','raw_hash'),)


class Snapshot(Base):
    __tablename__ = 'corpus_snapshot'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    parser_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default='staged')
    search_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = 'snapshot_document'
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('corpus_snapshot.id'), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('document_version.id'), primary_key=True)


class Span(Base):
    __tablename__ = 'evidence_span'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('document_version.id'),index=True)
    parser_version: Mapped[str] = mapped_column(String(80))
    parent_id: Mapped[str|None] = mapped_column(ForeignKey('evidence_span.id'),nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(Text)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(30))
    text_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (CheckConstraint('start >= 0 AND "end" > start',name='span_bounds'),)


class Term(Base):
    __tablename__ = 'defined_term'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('document_version.id'),index=True)
    span_id: Mapped[str] = mapped_column(ForeignKey('evidence_span.id'))
    name: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    location_only: Mapped[bool] = mapped_column(Boolean)


class TermUse(Base):
    __tablename__ = 'term_occurrence'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('evidence_span.id'),index=True)
    term_id: Mapped[str] = mapped_column(ForeignKey('defined_term.id'))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)


class Reference(Base):
    __tablename__ = 'reference_mention'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('evidence_span.id'),index=True)
    target_id: Mapped[str|None] = mapped_column(ForeignKey('evidence_span.id'),nullable=True)
    literal: Mapped[str] = mapped_column(Text)
    target_label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)


class Issue(Base):
    __tablename__ = 'extraction_issue'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('document_version.id'),index=True)
    parser_version: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = 'ingestion_job'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160),unique=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('corpus_snapshot.id'))
    status: Mapped[str] = mapped_column(String(30),default='queued')
    stage: Mapped[str] = mapped_column(String(80),default='registered')
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    lease_token: Mapped[str|None] = mapped_column(String(36),nullable=True)
    lease_until: Mapped[datetime|None] = mapped_column(DateTime(timezone=True),nullable=True)
    error_code: Mapped[str|None] = mapped_column(String(100),nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class Artifact(Base):
    __tablename__ = 'index_artifact'
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('corpus_snapshot.id'),primary_key=True)
    stage: Mapped[str] = mapped_column(String(80),primary_key=True)
    detail: Mapped[dict] = mapped_column(JSON)


def engine(settings: Settings):
    return create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True,
                         connect_args={'connect_timeout':3}, hide_parameters=True)
