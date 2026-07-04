"""add_print_jobs_tables

Revision ID: b2c3d4e5f6a1
Revises: 033d388a81e3
Create Date: 2026-07-05 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a1'
down_revision: Union[str, None] = '033d388a81e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'print_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('print_name', sa.String(200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('printed_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_print_jobs_printed_at', 'print_jobs', ['printed_at'])

    op.create_table(
        'print_job_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('print_job_id', sa.Integer(), nullable=False),
        sa.Column('spool_id', sa.Integer(), nullable=True),
        sa.Column('spool_code', sa.String(64), nullable=False),
        sa.Column('product_name', sa.String(200), nullable=False),
        sa.Column('used_g', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['print_job_id'], ['print_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['spool_id'], ['spools.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_print_job_lines_print_job_id', 'print_job_lines', ['print_job_id'])
    op.create_index('ix_print_job_lines_spool_id', 'print_job_lines', ['spool_id'])


def downgrade() -> None:
    op.drop_index('ix_print_job_lines_spool_id', table_name='print_job_lines')
    op.drop_index('ix_print_job_lines_print_job_id', table_name='print_job_lines')
    op.drop_table('print_job_lines')
    op.drop_index('ix_print_jobs_printed_at', table_name='print_jobs')
    op.drop_table('print_jobs')
