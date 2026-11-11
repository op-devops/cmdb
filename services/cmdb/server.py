"""对内 gRPC 服务:随机端口启动 + 注册到 Nacos,配置全部来自 Nacos(cmdb-service.yaml)。

只对内暴露,不发布端口;扩容靠多起实例,调用方通过 Nacos 发现并加权随机选实例。
"""

import logging
import os
import pathlib
import signal
import threading
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from paramiko import pkey

import cmdb.v1.cmdb_pb2
from libs.common.config import BootstrapSettings
from libs.common.access_log import AccessLogServerInterceptor
from libs.common.grpc_auth import TokenServerInterceptor
from libs.common.request_id import RequestIdServerInterceptor, install_log_filter
from libs.common.nacos_client import NacosClient, RemoteConfig, local_ip

from sqlalchemy.exc import IntegrityError

from cmdb.v1 import cmdb_pb2, cmdb_pb2_grpc
from services.cmdb.db import queries
from services.cmdb.db.session import init_engine, session_scope
from services.cmdb.redis_client import init_redis
from services.cmdb import ssh

logging.basicConfig(
    level=logging.INFO,
    # %(filename)s:%(lineno)d = 打这条日志的源文件和行号
    # [%(request_id)s] 由 libs.common.request_id 的日志过滤器注入,
    # 同一条请求在网关和各服务的日志里是同一个 ID
    format="%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(filename)s:%(lineno)d %(message)s",
)
install_log_filter()

logger = logging.getLogger("cmdb-service")

DATA_ID = "cmdb-service.yaml"
# 内部 gRPC 调用令牌 + 免鉴权名单,网关和服务共读这一份
SECURITY_DATA_ID = "cmdb-security.yaml"


class CmdbService(cmdb_pb2_grpc.CmdbServiceServicer):
    def __init__(self, cfg: RemoteConfig, sec: RemoteConfig, served_by: str):
        self._cfg = cfg
        self._served_by = served_by
        self._sec = sec

    def Ping(self, request, context):
        logger.info("Ping: %s", request.message)
        return cmdb_pb2.PingResponse(
            message=f"pong: {request.message}", served_by=self._served_by
        )

    # ================= 辅助 =================

    @staticmethod
    def _to_host_info(host) -> cmdb_pb2.HostInfo:
        return cmdb_pb2.HostInfo(
            id=host.id, name=host.name, hostname=host.hostname, port=host.port,
            username=host.username, desc=host.desc or "", is_verified=host.is_verified,
            has_pkey=bool(host.pkey), created_at=host.created_at or "",
            created_by=host.created_by or "", category_id=host.category_id or "",
        )

    @staticmethod
    def _in_scope(scope, category_id) -> bool:
        """主机分类数据权限判定。category_id 空/None 视为未分类(哨兵 none)。"""
        if scope.host_category_all:
            return True
        cat = (category_id or "").strip() or "none"
        return cat in set(scope.allowed_categories)

    @staticmethod
    def _check_host_fields(request, context) -> tuple[str, str, str]:
        """网关已经校验过一遍,这里再兜一次:服务可能被别的调用方直接调。"""
        name = request.name.strip()
        hostname = request.hostname.strip()
        username = request.username.strip()
        if not name:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "请输入主机名称")
        if not hostname:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "请输入主机名或IP")
        if not username:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "请输入登录用户名")
        if not 1 <= request.port <= 65535:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "SSH 端口范围 1-65535")
        return name, hostname, username

    # ================= 主机 =================

    def ListHosts(self, request, context):
        page = request.page or 1
        page_size = min(request.page_size or 20, 200)
        scope = request.scope
        # all=True -> None(不限);否则限制在允许集合(空集合 => 看不到任何主机)
        allowed = None if scope.host_category_all else list(scope.allowed_categories)
        with session_scope() as session:
            hosts, total = queries.list_hosts(session, request.keyword.strip(), request.category_id.strip(),
                                              page, page_size,scope_categories=allowed)
            items = [self._to_host_info(h) for h in hosts]
        return cmdb_pb2.ListHostsResponse(
            hosts=items,
            pagination=cmdb_pb2.Pagination(page=page, page_size=page_size, total=total),
        )

    def GetHost(self, request, context):
        with session_scope() as session:
            host = queries.get_host(session, request.id)
            in_scope = host is not None and self._in_scope(request.scope, host.category_id)
            info = self._to_host_info(host) if host else None
        if info is None or not in_scope:
            context.abort(grpc.StatusCode.NOT_FOUND, "未找到指定主机")
        return cmdb_pb2.GetHostResponse(host=info)

    def CreateHost(self, request, context):
        name, hostname, username = self._check_host_fields(request, context)
        if not self._in_scope(request.scope, request.category_id.strip()):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "无权在该分类下新建主机")
        duplicated = False
        try:
            with session_scope() as session:
                if queries.get_host_by_name(session, name) is not None:
                    duplicated = True
                else:
                    host = queries.create_host(
                        session, name=name, hostname=hostname, port=request.port,
                        username=username, pkey=request.pkey if request.pkey.strip() else None,
                        desc=request.desc.strip(), category_id=request.category_id.strip() or None,
                        created_by=request.operator_id or None,
                    )
                    info = self._to_host_info(host)
        except IntegrityError:
            # 两个请求同时建同名主机:上面的查重都通过了,由唯一索引兜底
            duplicated = True
        if duplicated:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, f"已存在的主机名称【{name}】")
        logger.info("新建主机 %s(%s) by %s", name, hostname, request.operator_id)
        return cmdb_pb2.CreateHostResponse(host=info)

    def UpdateHost(self, request, context):
        name, hostname, username = self._check_host_fields(request, context)
        fields = dict(
            name=name, hostname=hostname, port=request.port,
            username=username, desc=request.desc.strip(),
            category_id=request.category_id.strip() or None,
        )
        # 没传 = 不改;传空串 = 清掉独立密钥
        if request.HasField("pkey"):
            fields["pkey"] = request.pkey if request.pkey.strip() else None

        error = None
        try:
            with session_scope() as session:
                host = queries.get_host(session, request.id)
                other = queries.get_host_by_name(session, name)
                if host is None:
                    error = (grpc.StatusCode.NOT_FOUND, "未找到指定主机")
                elif not self._in_scope(request.scope, host.category_id):
                    error = (grpc.StatusCode.NOT_FOUND, "未找到指定主机")
                elif not self._in_scope(request.scope, request.category_id.strip()):
                    error = (grpc.StatusCode.PERMISSION_DENIED, "无权把主机移动到该分类")
                elif other is not None and other.id != host.id:
                    error = (grpc.StatusCode.ALREADY_EXISTS, f"已存在的主机名称【{name}】")
                else:
                    queries.update_host(host, updated_by=request.operator_id or None, **fields)
                    session.flush()
                    info = self._to_host_info(host)
        except IntegrityError:
            error = (grpc.StatusCode.ALREADY_EXISTS, f"已存在的主机名称【{name}】")
        if error:
            context.abort(*error)
        logger.info("编辑主机 %s by %s", request.id, request.operator_id)
        return cmdb_pb2.UpdateHostResponse(host=info)

    def DeleteHost(self, request, context):
        # 老平台删之前会查发布配置、计划任务、监控、执行模板有没有引用这台主机。
        # 那几个模块还没迁过来,迁过来之后在这里补上检查。
        with session_scope() as session:
            host = queries.get_host(session, request.id)
            missing = host is None or not self._in_scope(request.scope, host.category_id)
            if not missing:
                queries.delete_host(session, request.id)
        if missing:
            context.abort(grpc.StatusCode.NOT_FOUND, "未找到指定主机")
        logger.info("删除主机 %s by %s", request.id, request.operator_id)
        return cmdb_pb2.DeleteHostResponse()

    # ================= 主机分类 =================
    @staticmethod
    def _to_category_info(cat, host_count: int = 0) -> cmdb_pb2.CategoryInfo:
        return cmdb_pb2.CategoryInfo(
            id=cat.id, name=cat.name, sort=cat.sort, host_count=host_count)

    def ListCategories(self, request, context):
        with session_scope() as session:
          rows = queries.list_categories(session)
          items = [self._to_category_info(cat, cnt) for cat, cnt in rows]
        return cmdb_pb2.ListCategoriesResponse(categories=items)

    def CreateCategory(self, request, context):
        name = request.name.strip()
        if not name:
          context.abort(grpc.StatusCode.INVALID_ARGUMENT, "请输入分类名称")
        duplicated = False
        try:
          with session_scope() as session:
              if queries.get_category_by_name(session, name) is not None:
                  duplicated = True
              else:
                  cat = queries.create_category(
                      session, name=name, created_by=request.operator_id or None)
                  info = self._to_category_info(cat, 0)
        except IntegrityError:
          duplicated = True
        if duplicated:
          context.abort(grpc.StatusCode.ALREADY_EXISTS, f"已存在的分类【{name}】")
        logger.info("新建主机分类 %s by %s", name, request.operator_id)
        return cmdb_pb2.CreateCategoryResponse(category=info)

    def DeleteCategory(self, request, context):
        with session_scope() as session:
          existed = queries.delete_category(session, request.id)
        if not existed:
          context.abort(grpc.StatusCode.NOT_FOUND, "未找到指定分类")
        logger.info("删除主机分类 %s by %s", request.id, request.operator_id)
        return cmdb_pb2.DeleteCategoryResponse()

    def _verify_ssh(self, hostname, port ,username, pkey, password, context) -> str:
        """连得上返回空串,连不上返回中文原因。需要全局密钥但平台没配时 abort。"""
        global_priv = self._sec.get("ssh.private_key")
        global_pub = self._sec.get("ssh.public_key")
        if not global_priv or not global_pub:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "平台未配置全局密钥,无法验证密码类主机")
        try:
            # 配了独立密钥:直接用它,不碰全局密钥、不改主机
            if pkey:
                ssh.connect(hostname,port,username,pkey=pkey).close()
                return ""

            # 传了密码 = 首次验证:用密码登录,把平台公钥装上,密码用完即丢
            if password:
                client = ssh.connect(hostname,port,username,password=password)
                try:
                    ssh.add_public_key(client, global_pub)
                finally:
                    client.close()
            # 用平台私钥连一次,确认以后能免密登录
            ssh.connect(hostname,port,username,pkey=global_priv).close()
            return  ""
        except ssh.SSHError as e:
            return str(e)

    def VerifyHost(self, request, context):
        # 1) 先把连接要用的信息取出来。SSH 可能要几秒,别占着数据库连接,所以读完就退出 session,连上之后再开一个新 session 写状态。
        with session_scope() as session:
            host = queries.get_host(session, request.id)
            missing = host is None or not self._in_scope(request.scope, host.category_id)
            if not missing:
                target = (host.hostname, host.port, host.username, host.pkey)
            if missing:
                context.abort(grpc.StatusCode.NOT_FOUND, "未找到指定主机")
        # 2) 真正去连。连不上是主机侧的问题(密码错/网络不通),返回失败原因让用户处理,不是服务器 500;缺全局密钥这种平台配置问题才 abort。
        error = self._verify_ssh(*target, request.password, context)
        if error:
            logger.info("验证主机 %s 失败: %s", request.id, error)
            return cmdb.v1.cmdb_pb2.VerifyHostResponse(is_verified=False,message=error)
        # 3) 通过 -> 标记已验证
        with session_scope() as session:
            host = queries.get_host(session, request.id)
            if host is not None:
                queries.mark_host_verified(host,updated_by=request.operator_id or None)
        logger.info("验证主机 %s 成功 by %s", request.id, request.operator_id)
        return cmdb_pb2.VerifyHostResponse(is_verified=True,message="验证完成")

    def GetShellCredential(self, request, context):
        with session_scope() as session:
            host = queries.get_host(session, request.id)
            if host is None or not self._in_scope(request.scope, host.category_id):
                context.abort(grpc.StatusCode.NOT_FOUND, "未找到指定主机")
            hostname, port, username = host.hostname, host.port, host.username
            pkey, verified = host.pkey, host.is_verified
            # 没有独立密钥就用平台全局私钥(跟验证时同一把)
            key = pkey or self._sec.get("ssh.private_key", "")
            if not key:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION,"主机没有独立密钥,平台也没配全局密钥")
            return cmdb_pb2.GetShellCredentialResponse(hostname=hostname,port=port,username=username,pkey=key,is_verified=verified)

def _apply_log_level(data: dict) -> None:
    logging.getLogger().setLevel((data.get("log") or {}).get("level", "INFO"))


def main() -> None:
    client = NacosClient(BootstrapSettings())
    cfg = RemoteConfig(client, DATA_ID)
    sec = RemoteConfig(client, SECURITY_DATA_ID)
    _apply_log_level(cfg.data)
    cfg.on_change(_apply_log_level)

    init_engine(
        cfg.get("db.dsn"),
        pool_size=int(cfg.get("db.pool_size", 5)),
        max_overflow=int(cfg.get("db.max_overflow", 10)),
    )
    init_redis(
        cfg.get("redis.host", "redis"),
        int(cfg.get("redis.port", 6379)),
        cfg.get("redis.password", ""),
        int(cfg.get("redis.db", 0)),
    )

    host = cfg.get("grpc.host", "0.0.0.0")
    port = int(cfg.get("grpc.port", 0))
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=int(cfg.get("grpc.max_workers", 10))),
        # 共享令牌门禁,每次调用现取 -> Nacos 上改令牌不用重启
        interceptors=(
            # 顺序:先有请求 ID,再记访问日志,最后校验令牌——
            # 这样被令牌挡掉的调用也会留下一条带 ID 的访问日志
            RequestIdServerInterceptor(),
            AccessLogServerInterceptor(),
            TokenServerInterceptor(
                lambda: sec.get("grpc.token", ""),
                lambda: sec.get("grpc.exempt_methods"),
            ),
        ),
    )
    port = server.add_insecure_port(f"{host}:{port}")
    if port == 0:
        raise RuntimeError(f"端口绑定失败(host={host},配置端口被占用?)")

    service_name = cfg.get("service.name", "cmdb-service")
    ip = cfg.get("service.ip") or local_ip()
    cmdb_pb2_grpc.add_CmdbServiceServicer_to_server(
        CmdbService(cfg, sec, f"{ip}:{port}"), server
    )
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    server.start()
    logger.info("cmdb-service 已启动 %s:%s", host, port)
    # 随机端口写文件,给容器 healthcheck(scripts/grpc_healthcheck.py)用
    pathlib.Path(os.environ.get("GRPC_PORT_FILE", "/tmp/grpc.port")).write_text(str(port))

    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("cmdb.v1.CmdbService", health_pb2.HealthCheckResponse.SERVING)

    client.register(service_name, ip, port, weight=float(cfg.get("service.weight", 1.0)))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    logger.info("正在退出:置 NOT_SERVING、从 Nacos 摘除实例并优雅停机")
    health_servicer.enter_graceful_shutdown()
    client.deregister(service_name, ip, port)
    server.stop(grace=5).wait()


if __name__ == "__main__":
    main()
