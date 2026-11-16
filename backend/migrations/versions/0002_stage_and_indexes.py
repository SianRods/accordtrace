"""Stage identifiers and canonical lookup indexes."""
from alembic import op
import sqlalchemy as sa

revision = '0002_stage_and_indexes'
down_revision = '0001_canonical'
branch_labels = None
depends_on = None

INDEXES = (
    ('document_version', 'agreement_id'),
    ('evidence_span', 'document_id'),
    ('extraction_issue', 'document_id'),
    ('defined_term', 'document_id'),
    ('reference_mention', 'source_id'),
    ('term_occurrence', 'source_id'),
)

def upgrade():
    op.alter_column('ingestion_job', 'stage', type_=sa.String(80), existing_type=sa.String(40))
    for table, column in INDEXES:
        op.create_index(f'ix_{table}_{column}', table, [column])

def downgrade():
    for table, column in reversed(INDEXES):
        op.drop_index(f'ix_{table}_{column}', table_name=table)
    op.alter_column('ingestion_job', 'stage', type_=sa.String(40), existing_type=sa.String(80))
