"""engine/session 工厂。DSN 来自 Nacos(db.dsn),用的是只有 DML 权限的 app 账号。"""

from contextlib import contextmanager

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(dsn: str, pool_size: int = 5, max_overflow: int = 10) -> None:
    global _engine, _SessionLocal
    url = make_url(dsn)
    # 不设超时的话,地址被防火墙丢包时连接会一直卡住(psycopg 默认不超时,pymysql 默认 10s)
    connect_args = {"connect_timeout": 3} if url.get_backend_name() in ("mysql", "postgresql") else {}
    _engine = create_engine(
        url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow, connect_args=connect_args
    )
    # create_engine 不真正连接,pool_pre_ping 也只在取连接时才检查——不在这里探一次的话,
    # 数据库挂着服务也能"正常启动",要等第一个请求进来才报错。
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        raise RuntimeError(
            f"数据库连接失败 {url.render_as_string(hide_password=True)}: {e.__cause__ or e}"
        ) from e
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("db engine 还没初始化,先调用 init_engine(dsn)")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
