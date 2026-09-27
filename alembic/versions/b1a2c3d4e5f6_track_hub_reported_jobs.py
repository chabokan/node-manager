"""track hub reporting for finished jobs

Revision ID: b1a2c3d4e5f6
Revises: c4a0e82f5a21

Finished jobs whose result was never acknowledged by the hub are retried by the
auto-heal worker. Historical completed rows are marked reported so enabling the
feature does not replay old job results.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1a2c3d4e5f6'
down_revision: Union[str, None] = 'c4a0e82f5a21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('server_root_jobs', sa.Column('reported', sa.Boolean(), nullable=True))
    op.execute("UPDATE server_root_jobs SET reported = 1 WHERE completed_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column('server_root_jobs', 'reported')
