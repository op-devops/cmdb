"""初始表:items(示例,照着改成你自己的表)

Revision ID: 0001_init
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_items_name", "items", ["name"], unique=True)


def downgrade() -> None:
    op.drop_table("items")
