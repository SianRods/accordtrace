"""Opt-in acceptance checks against the disposable local Compose services.

No database resets. Real corpus artifacts persist; synthetic graph nodes are scoped
to a random test snapshot and removed in finally. Never run against production.
"""
import json
import os
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from accordtrace_backend import graph, ingestion
from accordtrace_backend.api.app import create_app
from accordtrace_backend.core import DomainError, PARSER_VERSION, Settings, digest
from accordtrace_backend.storage import (Artifact, Document, Job, Membership,
    Reference, Snapshot, Span, Term, TermUse, engine, utcnow)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get('ACCORDTRACE_INTEGRATION') != '1', reason='Requires local Compose services')]


def test_live_ingestion_recovery_projection_and_evidence(monkeypatch):
    settings = Settings()
    db_engine = engine(settings)
    key = 'acceptance-' + str(uuid.uuid4())
    before, _ = ingestion.sources(settings)
    job = ingestion.enqueue(settings, db_engine, key)
    assert ingestion.enqueue(settings, db_engine, key).id == job.id
    claimed = ingestion.claim(db_engine, settings.lease_seconds)
    assert claimed[0] == job.id
    real_project = graph.project

    def interrupted(*args, **kwargs):
        raise RuntimeError('injected_graph_interruption')

    # Fail only after canonical stages commit; recovery must reuse their IDs.
    monkeypatch.setattr(graph, 'project', interrupted)
    with pytest.raises(RuntimeError, match='injected_graph_interruption'):
        ingestion.process(settings, db_engine, claimed)
    with Session(db_engine) as db:
        assert db.get(Job, job.id).status == 'failed'
        assert db.scalar(select(func.count()).select_from(Membership)
                         .where(Membership.snapshot_id == job.snapshot_id)) == 6
        assert db.scalar(select(func.count()).select_from(Artifact)
                         .where(Artifact.snapshot_id == job.snapshot_id,
                                Artifact.stage.like('canonical:%'))) == 6
    ingestion.retry(db_engine, job.id)
    old = ingestion.claim(db_engine, settings.lease_seconds)
    # Simulate a crashed worker's expired lease without waiting two minutes.
    with Session(db_engine) as db, db.begin():
        db.get(Job, job.id).lease_until = utcnow() - timedelta(seconds=1)
    recovered = ingestion.claim(db_engine, settings.lease_seconds)
    assert recovered[0] == old[0] and recovered[1] != old[1]
    with Session(db_engine) as db, db.begin():
        with pytest.raises(DomainError, match='job_lease_lost'):
            ingestion.owned(db, old[0], old[1], settings.lease_seconds)
    monkeypatch.setattr(graph, 'project', real_project)
    assert ingestion.process(settings, db_engine, recovered)

    def counts():
        with Session(db_engine) as db:
            docs = select(Membership.document_id).where(Membership.snapshot_id == job.snapshot_id)
            spans = select(Span.id).where(Span.document_id.in_(docs), Span.parser_version == PARSER_VERSION)
            statements = {
                'documents': select(func.count()).select_from(Document).where(Document.id.in_(docs)),
                'spans': select(func.count()).select_from(Span).where(Span.id.in_(spans)),
                'terms': select(func.count()).select_from(Term).where(Term.span_id.in_(spans)),
                'references': select(func.count()).select_from(Reference).where(Reference.source_id.in_(spans)),
                'term_uses': select(func.count()).select_from(TermUse).where(TermUse.source_id.in_(spans)),
                'memberships': select(func.count()).select_from(Membership).where(Membership.snapshot_id == job.snapshot_id),
            }
            return {name: db.scalar(stmt) for name, stmt in statements.items()}

    canonical_counts = counts()
    with Session(db_engine) as db:
        snapshot = db.get(Snapshot, job.snapshot_id)
        assert snapshot.status == 'graph_ready' and not snapshot.search_ready
        report = snapshot.report
        assert db.get(Job, job.id).attempts == 3
        nodes, edges = graph.records(db, job.snapshot_id, PARSER_VERSION)
        assert report['graph'] == {'nodes': len(nodes), 'edges': len(edges)}
    # A different request key for identical sources must not duplicate the KB.
    again = ingestion.enqueue(settings, db_engine, key + '-repeat')
    assert again.snapshot_id == job.snapshot_id
    assert ingestion.process(settings, db_engine, ingestion.claim(db_engine, settings.lease_seconds))
    assert counts() == canonical_counts

    # Genuine PostgreSQL constraints; both rejected writes roll back.
    with Session(db_engine) as db:
        original = db.scalar(select(Document))
        duplicate = {c.name: getattr(original, c.name) for c in Document.__table__.columns}
        duplicate['id'] = str(uuid.uuid4())
        db.add(Document(**duplicate))
        with pytest.raises(IntegrityError): db.commit()
        db.rollback()
        db.add(Membership(snapshot_id=str(uuid.uuid4()), document_id=original.id))
        with pytest.raises(IntegrityError): db.commit()
        db.rollback()

    headers = {'Authorization': 'Bearer ' + settings.api_token.get_secret_value()}
    anchors = []
    with TestClient(create_app(settings)) as client:
        ready = client.get('/health/ready')
        assert ready.status_code == 200 and ready.json()['graph_ready']
        assert not ready.json()['search_ready']
        docs = client.get('/api/v1/documents', headers=headers).json()['items']
        assert len(docs) == 6
        for doc in docs:
            sections = client.get(f"/api/v1/documents/{doc['id']}/sections", headers=headers).json()['items']
            assert sections
            evidence = client.get('/api/v1/evidence/' + sections[0]['id'], headers=headers)
            assert evidence.status_code == 200
            item = evidence.json()
            with Session(db_engine) as db:
                text = db.get(Document, doc['id']).text
                assert item['quote'] == text[item['start']:item['end']]
                assert digest(item['quote'].encode()) == item['text_hash']
            anchors.append({k: item[k] for k in ('agreement_id', 'id', 'section_label', 'start', 'end')})
        assert client.get('/api/v1/documents').status_code == 401
        posted = client.post('/api/v1/admin/ingestions', json={'manifest': 'initial-six'},
            headers={**headers, 'Idempotency-Key': key})
        assert posted.status_code == 202 and posted.json()['id'] == job.id
        assert client.get('/api/v1/admin/jobs/' + job.id, headers=headers).json()['status'] == 'succeeded'
    after, _ = ingestion.sources(settings)
    assert [r['sha'] for r in before] == [r['sha'] for r in after]
    (settings.runtime / 'acceptance.json').write_text(json.dumps(dict(
        snapshot_id=job.snapshot_id, job_id=job.id, canonical_counts=canonical_counts,
        graph=report['graph'], anchors=anchors, documents=report['documents']), indent=2), encoding='utf-8')
    db_engine.dispose()


def test_live_graph_rejects_missing_target():
    settings = Settings()
    sid, gid = str(uuid.uuid4()), str(uuid.uuid4())
    nodes = [dict(label='DocumentVersion', graph_id=gid, snapshot_id=sid, canonical_id=gid)]
    edges = [dict(kind='REFERENCES', reference_id=str(uuid.uuid4()), source=gid,
                  target=str(uuid.uuid4()))]
    try:
        with pytest.raises(RuntimeError, match='graph_target_reconciliation_failed'):
            graph.project(settings, sid, nodes, edges, lambda: None)
    finally:
        with graph.driver(settings) as conn, conn.session() as session:
            session.run('MATCH (n:KBNode {snapshot_id:$sid}) DETACH DELETE n', sid=sid).consume()
