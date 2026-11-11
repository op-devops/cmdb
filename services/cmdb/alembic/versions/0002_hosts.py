"""主机表 hosts(迁自老运维平台 apps/host)

0001 建的 items 是脚手架示例表,这里不动它:已经 upgrade 过的库里如果真有数据,
删表得 DBA 自己决定。

Revision ID: 0002_hosts
Revises: 0001_init
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_hosts"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hosts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("pkey", sa.Text(), nullable=True),
        sa.Column("desc", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(26), nullable=True),
        sa.Column("updated_at", sa.String(20), nullable=True),
        sa.Column("updated_by", sa.String(26), nullable=True),
    )
    op.create_index("ix_hosts_name", "hosts", ["name"], unique=True)
    op.create_index("ix_hosts_hostname", "hosts", ["hostname"])


def downgrade() -> None:
    op.drop_table("hosts")
