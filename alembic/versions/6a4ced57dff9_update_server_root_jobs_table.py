"""update server root jobs table

Revision ID: 6a4ced57dff9
Revises: 75df73a890e1
Create Date: 2024-04-27 10:34:37.693835

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a4ced57dff9'
down_revision: Union[str, None] = '75df73a890e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('server_root_jobs', sa.Column('run_count', sa.Integer(), nullable=True))
    op.add_column('server_root_jobs', sa.Column('status', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('server_root_jobs', 'status')
    op.drop_column('server_root_jobs', 'run_count')
    # ### end Alembic commands ###
