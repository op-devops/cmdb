"""给 hosts 表和各列加数据库注释(COMMENT)

0002 已在别的库应用过,不能改历史迁移,单独加这一条把注释刷上去。纯元数据变更,不动数据。

注意:MySQL 改列注释是 MODIFY COLUMN,要重述整个列定义。所以每列都得带上和 0002
一致的 nullable / server_default,否则会把 NOT NULL 和 DEFAULT 一起改没了。

Revision ID: 0003_host_comments
Revises: 0002_hosts
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_host_comments"
down_revision = "0002_hosts"
branch_labels = None
depends_on = None

# (列名, 类型, nullable, server_default, 注释) —— 前四项必须跟 0002 建表时完全一致
_COLUMNS = [
    ("id",          sa.String(26),  False, None,             "主机ID"),
    ("name",        sa.String(100), False, None,             "主机名称,全局唯一"),
    ("hostname",    sa.String(255), False, None,             "主机名或IP"),
    ("port",        sa.Integer(),   False, sa.text("'22'"),  "SSH端口"),
    ("username",    sa.String(50),  False, None,             "SSH登录用户名"),
    ("pkey",        sa.Text(),      True,  None,             "独立私钥PEM,留空则用平台全局密钥"),
    ("desc",        sa.String(255), False, sa.text("''"),    "备注"),
    ("is_verified", sa.Boolean(),   False, sa.false(),       "SSH验证是否通过"),
    ("created_at",  sa.String(20),  False, None,             "创建时间"),
    ("created_by",  sa.String(26),  True,  None,             "创建人(auth用户ID)"),
    ("updated_at",  sa.String(20),  True,  None,             "更新时间"),
    ("updated_by",  sa.String(26),  True,  None,             "更新人(auth用户ID)"),
]


def upgrade() -> None:
    op.create_table_comment("hosts", "主机")
    for name, type_, nullable, default, comment in _COLUMNS:
        op.alter_column("hosts", name, existing_type=type_, existing_nullable=nullable,
                        existing_server_default=default, comment=comment)


def downgrade() -> None:
    op.drop_table_comment("hosts")
    for name, type_, nullable, default, _ in _COLUMNS:
        op.alter_column("hosts", name, existing_type=type_, existing_nullable=nullable,
                        existing_server_default=default, comment=None)
