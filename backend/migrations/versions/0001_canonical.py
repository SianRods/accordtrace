"""Frozen initial canonical schema; no imports from mutable application models."""
from alembic import op

revision = "0001_canonical"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute('\nCREATE TABLE corpus_snapshot (\n\tid VARCHAR(36) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tparser_version VARCHAR(80) NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tsearch_ready BOOLEAN NOT NULL, \n\treport JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (fingerprint)\n)\n\n')
    op.execute('\nCREATE TABLE document_version (\n\tid VARCHAR(36) NOT NULL, \n\tagreement_id VARCHAR(80) NOT NULL, \n\traw_hash VARCHAR(64) NOT NULL, \n\ttext_hash VARCHAR(64) NOT NULL, \n\ttext TEXT NOT NULL, \n\tsource_uri TEXT NOT NULL, \n\tsource_revision VARCHAR(80) NOT NULL, \n\tbyte_size INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (agreement_id, raw_hash)\n)\n\n')
    op.execute('\nCREATE TABLE evidence_span (\n\tid VARCHAR(36) NOT NULL, \n\tdocument_id VARCHAR(36) NOT NULL, \n\tparser_version VARCHAR(80) NOT NULL, \n\tparent_id VARCHAR(36), \n\tkind VARCHAR(20) NOT NULL, \n\tlabel TEXT NOT NULL, \n\tstart INTEGER NOT NULL, \n\t"end" INTEGER NOT NULL, \n\trole VARCHAR(30) NOT NULL, \n\ttext_hash VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT span_bounds CHECK (start >= 0 AND "end" > start), \n\tFOREIGN KEY(document_id) REFERENCES document_version (id), \n\tFOREIGN KEY(parent_id) REFERENCES evidence_span (id)\n)\n\n')
    op.execute('\nCREATE TABLE extraction_issue (\n\tid VARCHAR(36) NOT NULL, \n\tdocument_id VARCHAR(36) NOT NULL, \n\tparser_version VARCHAR(80) NOT NULL, \n\tdetail JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(document_id) REFERENCES document_version (id)\n)\n\n')
    op.execute('\nCREATE TABLE index_artifact (\n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tstage VARCHAR(80) NOT NULL, \n\tdetail JSON NOT NULL, \n\tPRIMARY KEY (snapshot_id, stage), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id)\n)\n\n')
    op.execute('\nCREATE TABLE ingestion_job (\n\tid VARCHAR(36) NOT NULL, \n\tidempotency_key VARCHAR(160) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tstatus VARCHAR(30) NOT NULL, \n\tstage VARCHAR(40) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tlease_token VARCHAR(36), \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\terror_code VARCHAR(100), \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (idempotency_key), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id)\n)\n\n')
    op.execute('\nCREATE TABLE snapshot_document (\n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tdocument_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (snapshot_id, document_id), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id), \n\tFOREIGN KEY(document_id) REFERENCES document_version (id)\n)\n\n')
    op.execute('\nCREATE TABLE defined_term (\n\tid VARCHAR(36) NOT NULL, \n\tdocument_id VARCHAR(36) NOT NULL, \n\tspan_id VARCHAR(36) NOT NULL, \n\tname TEXT NOT NULL, \n\tscope TEXT NOT NULL, \n\tlocation_only BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(document_id) REFERENCES document_version (id), \n\tFOREIGN KEY(span_id) REFERENCES evidence_span (id)\n)\n\n')
    op.execute('\nCREATE TABLE reference_mention (\n\tid VARCHAR(36) NOT NULL, \n\tsource_id VARCHAR(36) NOT NULL, \n\ttarget_id VARCHAR(36), \n\tliteral TEXT NOT NULL, \n\ttarget_label TEXT NOT NULL, \n\tstatus VARCHAR(30) NOT NULL, \n\tstart INTEGER NOT NULL, \n\t"end" INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_id) REFERENCES evidence_span (id), \n\tFOREIGN KEY(target_id) REFERENCES evidence_span (id)\n)\n\n')
    op.execute('\nCREATE TABLE term_occurrence (\n\tid VARCHAR(36) NOT NULL, \n\tsource_id VARCHAR(36) NOT NULL, \n\tterm_id VARCHAR(36) NOT NULL, \n\tstart INTEGER NOT NULL, \n\t"end" INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_id) REFERENCES evidence_span (id), \n\tFOREIGN KEY(term_id) REFERENCES defined_term (id)\n)\n\n')

def downgrade():
    op.drop_table('term_occurrence')
    op.drop_table('reference_mention')
    op.drop_table('defined_term')
    op.drop_table('snapshot_document')
    op.drop_table('ingestion_job')
    op.drop_table('index_artifact')
    op.drop_table('extraction_issue')
    op.drop_table('evidence_span')
    op.drop_table('document_version')
    op.drop_table('corpus_snapshot')
