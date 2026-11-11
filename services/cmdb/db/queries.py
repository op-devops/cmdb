"""所有查询写在这:关联靠 id 手动 join,不靠外键或 ORM relationship()。"""

import time
from datetime import datetime

import shortuuid
from sqlalchemy import func, or_, select, update, false
from sqlalchemy.orm import Session

from services.cmdb.db.models import Host, HostCategory

# 纯小写字母表(去掉 0/1/i/l/o 避免和数字/彼此看混),生成的 id 固定 26 位
_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
_ID_GENERATOR = shortuuid.ShortUUID(alphabet=_ALPHABET)
_TIME_LEN = 9      # base31 9 位够用到公元 2800 年
_RANDOM_LEN = 17   # 26 - 9,约 84 bit 随机


def _encode_time(ms: int) -> str:
    """毫秒时间戳编成定长 base31。字母表本身是 ASCII 升序,
    所以定长编码后字典序 == 时间序,ID 可以直接按字符串排序当时间用。"""
    out = []
    for _ in range(_TIME_LEN):
        ms, rem = divmod(ms, len(_ALPHABET))
        out.append(_ALPHABET[rem])
    return "".join(reversed(out))


def new_id() -> str:
    """时间前缀 + 随机后缀,总长 26(与 models.py 的 String(26) 一致)。

    加时间前缀不是为了防重复——原来的 uuid() 是 122 bit 随机,生成一万亿个的
    碰撞概率也只有 1e-13,本来就不会撞。真正的目的是让 InnoDB 聚簇索引近似
    顺序插入:随机主键会让每次 INSERT 落在 B+ 树随机位置,引起页分裂、页填充率
    下降和缓冲池抖动。
    """
    return _encode_time(int(time.time() * 1000)) + _ID_GENERATOR.random(length=_RANDOM_LEN)


def now_str() -> str:
    """跟老平台 human_datetime() 同一个格式,迁移过来的数据不用转换。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 主机 ----------


def list_hosts(
    session: Session, keyword: str = "", category_id: str = "", page: int = 1, page_size: int = 20,
    scope_categories: list[str] | None = None,   # None=不限;否则限制在此集合(含哨兵 none)
) -> tuple[list[Host], int]:
    """返回 (当页数据, 总条数)。keyword 同时匹配名称、地址、备注。"""
    conds = []
    if keyword:
        like = f"%{keyword}%"
        conds.append(or_(Host.name.like(like), Host.hostname.like(like), Host.desc.like(like)))
    if category_id == "none":
        conds.append(Host.category_id.is_(None))
    elif category_id:
        conds.append(Host.category_id == category_id)
    # 数据权限:限定在允许的分类集合内。空集合 => false(),一条也查不出
    if scope_categories is not None:
        allow = []
        ids = [c for c in scope_categories if c != "none"]
        if ids:
            allow.append(Host.category_id.in_(ids))
        if "none" in scope_categories:
            allow.append(Host.category_id.is_(None))
        conds.append(or_(*allow) if allow else false())
    total = session.scalar(select(func.count()).select_from(Host).where(*conds)) or 0
    stmt = (
        select(Host).where(*conds)
        .order_by(Host.id.desc())              # id 有时间前缀,倒序即最新优先
        .offset((max(page, 1) - 1) * page_size).limit(page_size)
    )
    return list(session.scalars(stmt)), total


def get_host(session: Session, host_id: str) -> Host | None:
    return session.get(Host, host_id)


def get_host_by_name(session: Session, name: str) -> Host | None:
    return session.scalar(select(Host).where(Host.name == name))


def create_host(
    session: Session, *, name: str, hostname: str, port: int, username: str,
    pkey: str | None, desc: str, category_id: str | None, created_by: str | None,
) -> Host:
    host = Host(
        id=new_id(), name=name, hostname=hostname, port=port, username=username,
        pkey=pkey or None, desc=desc, category_id=category_id or None, is_verified=False,
        created_at=now_str(), created_by=created_by,
    )
    session.add(host)
    session.flush()
    return host


def update_host(host: Host, *, updated_by: str | None, **fields) -> None:
    """地址、端口、账号、密钥任何一个变了,之前的验证结果就不作数了。"""
    if any(k in fields and fields[k] != getattr(host, k)
           for k in ("hostname", "port", "username", "pkey")):
        host.is_verified = False
    for key, value in fields.items():
        setattr(host, key, value)
    host.updated_at = now_str()
    host.updated_by = updated_by


def delete_host(session: Session, host_id: str) -> None:
    """硬删除,跟老平台一致。"""
    host = session.get(Host, host_id)
    if host is not None:
        session.delete(host)

def mark_host_verified(host: Host, *, updated_by: str | None) -> None:
  """SSH 验证通过后调用。只动验证状态,不碰主机的其它字段。"""
  host.is_verified = True
  host.updated_at = now_str()
  host.updated_by = updated_by


# ---------- 主机分类 ----------

def list_categories(session: Session) -> list[tuple[HostCategory, int]]:
    """返回 [(分类, 该分类下主机数)],按 sort、id 排序。空分类数为 0。"""
    stmt = (
      select(HostCategory, func.count(Host.id))
      .outerjoin(Host, Host.category_id == HostCategory.id)
      .group_by(HostCategory.id)
      .order_by(HostCategory.sort, HostCategory.id)
    )
    return [(cat, cnt) for cat, cnt in session.execute(stmt)]

def get_category_by_name(session: Session, name: str) -> HostCategory | None:
    return session.scalar(select(HostCategory).where(HostCategory.name == name))

def create_category(session: Session, *, name: str, created_by: str | None) -> HostCategory:
    cat = HostCategory(
      id=new_id(), name=name, sort=0, created_at=now_str(), created_by=created_by)
    session.add(cat)
    session.flush()
    return cat

def delete_category(session: Session, category_id: str) -> bool:
    """删分类:先把该分类下主机归为未分类(category_id 置空),再删分类。
    返回分类是否存在。不存在返回 False(幂等)。"""
    cat = session.get(HostCategory, category_id)
    if cat is None:
      return False
    session.execute(
      update(Host).where(Host.category_id == category_id).values(category_id=None))
    session.delete(cat)
    return True
