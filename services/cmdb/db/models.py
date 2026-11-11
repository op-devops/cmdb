"""ORM 只做读写映射,表结构由 DBA 用 alembic 手动维护(见 services/cmdb/alembic/)。

约定:不用外键约束,主键统一用短字符串 id;关联查询在 queries.py 里手写 join,不用 relationship()。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。alembic 的 env.py 靠 Base.metadata 找到全部表。"""


class Host(Base):
    """主机。迁自老运维平台 apps/host/models.py 的 hosts 表。

    跟老表的区别:
      - id 从自增整数改成 26 位字符串(全项目统一)
      - created_by 存 auth 服务的用户 id,不再是本库 users 表的外键
      - name 加了唯一索引(老表只在代码里查重,并发下会插出重名)
      - hostname 从 50 放宽到 255,放得下 IPv6 和长域名
      - 加了 updated_at / updated_by
    """

    __tablename__ = "hosts"
    __table_args__ = {"comment": "主机"}

    id: Mapped[str] = mapped_column(String(26), primary_key=True, comment="主机ID")
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="主机名称,全局唯一")
    hostname: Mapped[str] = mapped_column(String(255), index=True, comment="主机名或IP")
    port: Mapped[int] = mapped_column(Integer, default=22, comment="SSH端口")
    username: Mapped[str] = mapped_column(String(50), comment="SSH登录用户名")
    pkey: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="独立私钥PEM,留空则用平台全局密钥")
    desc: Mapped[str] = mapped_column(String(255), default="", comment="备注")
    category_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True, comment="所属分类ID,空=未分类")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, comment="SSH验证是否通过")
    created_at: Mapped[str] = mapped_column(String(20), comment="创建时间")
    created_by: Mapped[str | None] = mapped_column(String(26), nullable=True, comment="创建人(auth用户ID)")
    updated_at: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="更新时间")
    updated_by: Mapped[str | None] = mapped_column(String(26), nullable=True, comment="更新人(auth用户ID)")


class HostCategory(Base):
    """主机分类。一台主机归属一个分类(hosts.category_id);分类被删后,
    该分类下主机的 category_id 置空(归为"未分类"),不级联删主机。
    不用外键约束,一致性在 queries.py 里保证(跟 Host 的约定一致)。
    """

    __tablename__ = "host_categories"
    __table_args__ = {"comment": "主机分类"}

    id: Mapped[str] = mapped_column(String(26), primary_key=True, comment="分类ID")
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="分类名称,全局唯一")
    sort: Mapped[int] = mapped_column(Integer, default=0, comment="排序,小的在前")
    created_at: Mapped[str] = mapped_column(String(20), comment="创建时间")
    created_by: Mapped[str | None] = mapped_column(String(26), nullable=True, comment="创建人(auth用户ID)")
