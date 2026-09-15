"""Index server usage timestamps for period reports.

Revision ID: c4a0e82f5a21
Revises: 59a5e01a115b
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'c4a0e82f5a21'
down_revision: Union[str, None] = '59a5e01a115b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_server_usage_created', 'server_usage', ['created'])


def downgrade() -> None:
    op.drop_index('ix_server_usage_created', table_name='server_usage')
