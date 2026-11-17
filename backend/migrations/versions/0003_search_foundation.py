"""Search artifacts, immutable build configuration and publication authority."""
from alembic import op
import sqlalchemy as sa
revision = '0003_search_foundation'
down_revision = '0002_stage_and_indexes'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('\nCREATE TABLE embedding_profile (\n\tid VARCHAR(64) NOT NULL, \n\tconfiguration JSON NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n')
    op.execute('\nCREATE TABLE logical_document (\n\tid VARCHAR(80) NOT NULL, \n\tagreement_id VARCHAR(80) NOT NULL, \n\trole VARCHAR(30) NOT NULL, \n\tparent_id VARCHAR(80), \n\tapproved BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(parent_id) REFERENCES logical_document (id)\n)\n\n')
    op.execute('CREATE INDEX ix_logical_document_agreement_id ON logical_document (agreement_id)')
    op.execute('\nCREATE TABLE active_snapshot (\n\tid SERIAL NOT NULL, \n\tsnapshot_id VARCHAR(36), \n\tgeneration INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT singleton_active CHECK (id = 1), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id)\n)\n\n')
    op.execute('\nCREATE TABLE embedding_cache (\n\tid VARCHAR(64) NOT NULL, \n\tprofile_id VARCHAR(64) NOT NULL, \n\tinput_hash VARCHAR(64) NOT NULL, \n\ttask VARCHAR(40) NOT NULL, \n\tvector JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(profile_id) REFERENCES embedding_profile (id)\n)\n\n')
    op.execute('\nCREATE TABLE snapshot_activation (\n\tgeneration SERIAL NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tprevious_id VARCHAR(36), \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (generation), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id), \n\tFOREIGN KEY(previous_id) REFERENCES corpus_snapshot (id)\n)\n\n')
    op.execute('\nCREATE TABLE child_chunk (\n\tid VARCHAR(36) NOT NULL, \n\tdocument_id VARCHAR(36) NOT NULL, \n\tparent_id VARCHAR(36) NOT NULL, \n\tstart INTEGER NOT NULL, \n\t"end" INTEGER NOT NULL, \n\ttext_hash VARCHAR(64) NOT NULL, \n\tchunker_hash VARCHAR(64) NOT NULL, \n\tinput_hash VARCHAR(64) NOT NULL, \n\tembedding_input TEXT NOT NULL, \n\ttokens INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT chunk_bounds CHECK (start >= 0 AND "end" > start), \n\tFOREIGN KEY(document_id) REFERENCES document_version (id), \n\tFOREIGN KEY(parent_id) REFERENCES evidence_span (id)\n)\n\n')
    op.execute('CREATE INDEX ix_child_chunk_document_id ON child_chunk (document_id)')
    op.execute('CREATE INDEX ix_child_chunk_parent_id ON child_chunk (parent_id)')
    op.execute('\nCREATE TABLE snapshot_chunk (\n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tchunk_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (snapshot_id, chunk_id), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id), \n\tFOREIGN KEY(chunk_id) REFERENCES child_chunk (id)\n)\n\n')
    op.execute('\nCREATE TABLE snapshot_resolution (\n\tsnapshot_id VARCHAR(36) NOT NULL, \n\treference_id VARCHAR(36) NOT NULL, \n\ttarget_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (snapshot_id, reference_id), \n\tFOREIGN KEY(snapshot_id) REFERENCES corpus_snapshot (id), \n\tFOREIGN KEY(reference_id) REFERENCES reference_mention (id), \n\tFOREIGN KEY(target_id) REFERENCES evidence_span (id)\n)\n\n')
    op.add_column('document_version', sa.Column('logical_document_id',sa.String(80),sa.ForeignKey('logical_document.id'),nullable=True))
    op.add_column('corpus_snapshot',sa.Column('configuration',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    op.add_column('corpus_snapshot',sa.Column('base_snapshot_id',sa.String(36),sa.ForeignKey('corpus_snapshot.id'),nullable=True))
    op.add_column('ingestion_job',sa.Column('kind',sa.String(30),nullable=False,server_default='ingestion'))
    op.add_column('ingestion_job',sa.Column('configuration',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    op.execute("INSERT INTO logical_document (id,agreement_id,role,approved) SELECT DISTINCT agreement_id,agreement_id,'agreement',true FROM document_version")
    op.execute('UPDATE document_version SET logical_document_id=agreement_id')
    op.execute('INSERT INTO active_snapshot (id,generation) VALUES (1,0)')

def downgrade():
    raise RuntimeError('Phase 2 stores immutable history; downgrade requires an explicit retention/migration plan')
