"""add_print_job_files_and_status

Revision ID: aeafbed5c76f
Revises: c3d4e5f6a7b2
Create Date: 2026-08-03 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'aeafbed5c76f'
down_revision: Union[str, None] = 'c3d4e5f6a7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'print_job_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('print_job_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('link', 'upload', name='printfilekind'), nullable=False),
        sa.Column('provider', sa.String(50), nullable=True),
        sa.Column('url', sa.String(500), nullable=True),
        sa.Column('stored_filename', sa.String(255), nullable=True),
        sa.Column('original_filename', sa.String(255), nullable=True),
        sa.Column('file_ext', sa.String(10), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['print_job_id'], ['print_jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_print_job_files_print_job_id', 'print_job_files', ['print_job_id'])

    # Existing rows predate the planned/printing/done workflow and were already
    # deducted from filament at creation time, so they backfill as 'done'.
    op.add_column(
        'print_jobs',
        sa.Column(
            'status',
            sa.Enum('planned', 'printing', 'done', name='printjobstatus'),
            nullable=False,
            server_default='done',
        ),
    )
    op.add_column('print_jobs', sa.Column('completed_at', sa.DateTime(), nullable=True))
    op.create_index('ix_print_jobs_status', 'print_jobs', ['status'])
    op.execute('UPDATE print_jobs SET completed_at = printed_at WHERE completed_at IS NULL')


def downgrade() -> None:
    op.drop_index('ix_print_jobs_status', table_name='print_jobs')
    op.drop_column('print_jobs', 'completed_at')
    op.drop_column('print_jobs', 'status')

    op.drop_index('ix_print_job_files_print_job_id', table_name='print_job_files')
    op.drop_table('print_job_files')
