"""Preserve distinct document associations even when their raw bytes match."""
from alembic import op

revision='0004_logical_source_identity'
down_revision='0003_search_foundation'
branch_labels=None
depends_on=None


def upgrade():
    op.alter_column('document_version','logical_document_id',nullable=False)
    op.drop_constraint('document_version_agreement_id_raw_hash_key','document_version',type_='unique')
    op.create_unique_constraint('uq_document_logical_raw','document_version',['logical_document_id','raw_hash'])


def downgrade():
    raise RuntimeError('Logical source associations must be explicitly reconciled before downgrade')
