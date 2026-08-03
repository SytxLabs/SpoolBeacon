"""add_print_job_code

Revision ID: 4c0bf9e2ed81
Revises: aeafbed5c76f
Create Date: 2026-08-03 16:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4c0bf9e2ed81'
down_revision: Union[str, None] = 'aeafbed5c76f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('print_jobs', sa.Column('job_code', sa.String(64), nullable=True))
    # Existing rows predate job codes — backfill a unique deterministic value per row
    # before the column is locked down to NOT NULL + unique. Concatenate the raw id
    # (not zero-padded to a fixed width) so ids >= 10000 can't collide, e.g. 1000 vs 10000.
    op.execute("UPDATE print_jobs SET job_code = CONCAT('PJ-LEGACY-', id) WHERE job_code IS NULL")
    op.alter_column('print_jobs', 'job_code', existing_type=sa.String(64), nullable=False)
    op.create_index('ix_print_jobs_job_code', 'print_jobs', ['job_code'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_print_jobs_job_code', table_name='print_jobs')
    op.drop_column('print_jobs', 'job_code')
