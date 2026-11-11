"""alembic 环境:连接串只从 CMDB_MIGRATOR_DATABASE_URL 读(DDL 权限账号,DBA 手动跑)。

应用运行时那套 DML-only 账号在 Nacos 的 db.dsn 里,不在这用。
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from services.cmdb.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

dsn = os.environ.get("CMDB_MIGRATOR_DATABASE_URL")
if not dsn:
    raise SystemExit(
        "缺少环境变量 CMDB_MIGRATOR_DATABASE_URL(DDL 权限账号)。"
        "用 python3 dev.py db-upgrade,它会先把 .env 导进 shell。"
    )
config.set_main_option("sqlalchemy.url", dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
