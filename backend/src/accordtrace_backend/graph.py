"""Allowlisted, snapshot-isolated structural graph projection."""
from collections import Counter

from neo4j import GraphDatabase, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core import Settings, identity
from .storage import Document, Membership, Span, Term, TermUse, Reference, Resolution

LABELS = ('DocumentVersion','Section','Clause','DefinedTerm','UnresolvedReference')
EDGES = ('CONTAINS','DEFINES','USES_TERM','REFERENCES','HAS_UNRESOLVED_REFERENCE')


def driver(settings: Settings):
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user,
        settings.neo4j_password.get_secret_value()), connection_timeout=3,
        connection_acquisition_timeout=5, max_transaction_retry_time=5)


def records(db: Session, snapshot_id: str, parser_version: str):
    docs=db.scalars(select(Document).join(Membership, Membership.document_id==Document.id)
                    .where(Membership.snapshot_id==snapshot_id)).all()
    nodes, edges=[],[]
    resolutions={r.reference_id:r.target_id for r in db.scalars(select(Resolution).where(Resolution.snapshot_id==snapshot_id))}

    def node(label, key, **props):
        gid=identity(snapshot_id,key)
        nodes.append(dict(label=label, graph_id=gid, snapshot_id=snapshot_id, canonical_id=key, **props))

    def edge(kind, key, source, target, **props):
        edges.append(dict(kind=kind, reference_id=identity(snapshot_id,kind,key),
                          source=identity(snapshot_id,source),target=identity(snapshot_id,target),**props))

    for doc in docs:
        node('DocumentVersion',doc.id,agreement_id=doc.agreement_id,document_version_id=doc.id)
        spans=db.scalars(select(Span).where(Span.document_id==doc.id,Span.parser_version==parser_version)).all()
        span_ids=[s.id for s in spans]
        for s in spans:
            node('Section' if s.kind=='section' else 'Clause',s.id,document_version_id=doc.id,
                 agreement_id=doc.agreement_id,section_label=s.label,span_id=s.id,start=s.start,end=s.end)
            edge('CONTAINS',s.id,s.parent_id or doc.id,s.id)
        for term in db.scalars(select(Term).where(Term.span_id.in_(span_ids))):
            node('DefinedTerm',term.id,document_version_id=doc.id,name=term.name,scope=term.scope,
                 definition_span_id=term.span_id,location_only=term.location_only)
            if not term.location_only:
                edge('DEFINES',term.id,term.span_id,term.id)
        for use in db.scalars(select(TermUse).where(TermUse.source_id.in_(span_ids))):
            edge('USES_TERM',use.id,use.source_id,use.term_id,source_span_id=use.source_id,start=use.start,end=use.end)
        for ref in db.scalars(select(Reference).where(Reference.source_id.in_(span_ids))):
            if ref.status=='verified' or ref.id in resolutions:
                edge('REFERENCES',ref.id,ref.source_id,resolutions.get(ref.id,ref.target_id),source_span_id=ref.source_id,
                     literal_reference=ref.literal,start=ref.start,end=ref.end)
            else:
                node('UnresolvedReference',ref.id,document_version_id=doc.id,literal_reference=ref.literal,
                     resolution_status=ref.status,target_label=ref.target_label,start=ref.start,end=ref.end)
                edge('HAS_UNRESOLVED_REFERENCE',ref.id,ref.source_id,ref.id)
    return nodes,edges


def project(settings: Settings, snapshot_id, nodes, edges, heartbeat):
    counts=Counter()
    with driver(settings) as conn, conn.session() as session:
        session.run('CREATE CONSTRAINT kb_graph_id IF NOT EXISTS FOR (n:KBNode) REQUIRE n.graph_id IS UNIQUE').consume()
        session.run('CREATE INDEX kb_snapshot IF NOT EXISTS FOR (n:KBNode) ON (n.snapshot_id)').consume()
        session.run('CREATE INDEX kb_snapshot_document IF NOT EXISTS FOR (n:KBNode) ON (n.snapshot_id,n.document_version_id)').consume()
        for label in LABELS:
            rows=[{k:v for k,v in n.items() if k!='label'} for n in nodes if n['label']==label]
            for start in range(0,len(rows),250):
                heartbeat()
                session.run(Query(f'UNWIND $rows AS row MERGE (n:KBNode {{graph_id:row.graph_id}}) '
                                  f'SET n:{label} SET n += row',timeout=30),rows=rows[start:start+250]).consume()
        for kind in EDGES:
            rows=[{k:v for k,v in e.items() if k!='kind'} for e in edges if e['kind']==kind]
            for start in range(0,len(rows),250):
                heartbeat()
                batch=rows[start:start+250]
                value=session.run(Query('UNWIND $rows AS row MATCH (a:KBNode {graph_id:row.source}) '
                    'MATCH (b:KBNode {graph_id:row.target}) '
                    f'MERGE (a)-[r:{kind} {{reference_id:row.reference_id}}]->(b) SET r += row '
                    'RETURN count(r) AS total',timeout=30),rows=batch).single()['total']
                if value!=len(batch):
                    raise RuntimeError('graph_target_reconciliation_failed')
        actual_nodes=session.run('MATCH (n:KBNode {snapshot_id:$snapshot}) RETURN count(n) AS total',snapshot=snapshot_id).single()['total']
        actual_edges=session.run('MATCH (a:KBNode {snapshot_id:$snapshot})-[r]->(b:KBNode {snapshot_id:$snapshot}) RETURN count(r) AS total',snapshot=snapshot_id).single()['total']
        if (actual_nodes,actual_edges)!=(len(nodes),len(edges)):
            raise RuntimeError('graph_count_reconciliation_failed')
        counts.update(nodes=actual_nodes,edges=actual_edges)
    return dict(counts)
