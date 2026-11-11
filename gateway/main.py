"""对外 FastAPI 网关:南北向唯一入口。

运行配置来自 Nacos(data-id: cmdb-gateway.yaml),经 Nacos 服务发现调用内部 gRPC 服务。
"""

import logging
import os
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from libs.common.config import BootstrapSettings
from libs.common.grpc_auth import TokenClientInterceptor
from libs.common.request_id import RequestIdClientInterceptor, install_log_filter
from libs.common.grpc_resolver import NacosChannelPool
from libs.common.nacos_client import NacosClient, RemoteConfig, local_ip

from gateway.middlewares.access_log import AccessLogMiddleware
from gateway.middlewares.error_handler import ErrorHandlerMiddleware
from gateway.middlewares.request_id import RequestIdMiddleware
from gateway.middlewares.exception_handlers import http_exception_handler, validation_exception_handler
from gateway.lib.rate_limit import init_redis
from gateway.urls import register

logging.basicConfig(
    level=logging.INFO,
    # %(filename)s:%(lineno)d = 打这条日志的源文件和行号
    # [%(request_id)s] 由 libs.common.request_id 的日志过滤器注入,
    # 同一条请求在网关和各服务的日志里是同一个 ID
    format="%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(filename)s:%(lineno)d %(message)s",
)
install_log_filter()

logger = logging.getLogger("gateway")

DATA_ID = "cmdb-gateway.yaml"
SECURITY_DATA_ID = "cmdb-security.yaml"


def _apply_log_level(data: dict) -> None:
    logging.getLogger().setLevel((data.get("log") or {}).get("level", "INFO"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = NacosClient(BootstrapSettings())
    cfg = RemoteConfig(client, DATA_ID)
    sec = RemoteConfig(client, SECURITY_DATA_ID)
    _apply_log_level(cfg.data)
    cfg.on_change(_apply_log_level)
    app.state.cfg = cfg

    # 同步接口跑在 anyio 线程池里;limiter 绑定当前 event loop,只能在这里设一次
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(
        cfg.get("http.thread_pool_size", 40)
    )
    # 限速计数器。连接参数与内部服务同一套共享 Redis,但 key 前缀不同,互不干扰。
    init_redis(
        host=cfg.get("redis.host", "redis"),
        port=int(cfg.get("redis.port", 6379)),
        password=cfg.get("redis.password", ""),
        db=int(cfg.get("redis.db", 0)),
    )
    app.state.cmdb_pool = NacosChannelPool(
        client, cfg.get("upstreams.cmdb_service", "cmdb-service"),
        # 所有出站 gRPC 调用自动带上内部令牌,调用点无感
        interceptors=[TokenClientInterceptor(lambda: sec.get("grpc.token", "")), RequestIdClientInterceptor()],
    )
    # 登录校验走 auth-service 的 VerifyToken。
    # 带的令牌放在本项目自己的 security 配置(grpc.upstream_tokens.auth-service),
    # 值是 auth 的 grpc.token 抄过来。只读自己一份配置,不读别人的文件。
    app.state.auth_pool = NacosChannelPool(
        client, cfg.get("upstreams.auth_service", "auth-service"),
        interceptors=[TokenClientInterceptor(lambda: sec.get("grpc.upstream_tokens.auth-service", "")),
                      RequestIdClientInterceptor()],
    )

    # 注册到 Nacos,让上层网关(APISIX 等)用服务发现找到本实例,而不是在路由里写死地址。
    # 端口来自 GATEWAY_BOUND_PORT:__main__ 里先绑 socket 再把实际端口写进环境变量,
    # 因为 uvicorn 的 lifespan 跑在 create_server 之前,这里读不到 port=0 的真实端口。
    gw_name = cfg.get("service.name", "cmdb-gateway")
    gw_ip = cfg.get("service.ip") or local_ip()
    gw_port = int(os.environ.get("GATEWAY_BOUND_PORT") or cfg.get("http.port", 0))
    client.register(gw_name, gw_ip, gw_port, weight=float(cfg.get("service.weight", 1.0)))
    logger.info("网关已注册到 Nacos: %s -> %s:%s", gw_name, gw_ip, gw_port)

    yield

    logger.info("正在退出:从 Nacos 摘除网关实例")
    client.deregister(gw_name, gw_ip, gw_port)
    app.state.cmdb_pool.close()
    app.state.auth_pool.close()


app = FastAPI(title="gateway", lifespan=lifespan)
# add_middleware 后加的在外层,所以实际执行顺序是自下而上:
#   RequestId(生成 ID) -> AccessLog(计时、记一条) -> ErrorHandler(兜未处理异常) -> 路由
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
# 路由全在 gateway/urls.py 里登记,加接口不用改这个文件
register(app)


if __name__ == "__main__":
    # 监听地址/端口/信任代理网段全部来自 Nacos。http.port 配 0 表示随机端口——
    # 网关注册到 Nacos 之后,上层网关用服务发现找它,地址不需要固定。
    #
    # 这里手动 bind_socket 而不是直接 uvicorn.run(port=0):
    # uvicorn 的 lifespan 跑在 create_server 之前,port=0 时 lifespan 里读不到
    # 真实端口,就没法注册。先绑好 socket 拿到端口写进环境变量,再把 socket 交给
    # uvicorn——这正是 uvicorn.run 内部的做法,没有"先绑后放"的竞态。
    import uvicorn
    from uvicorn.supervisors import ChangeReload

    _cfg = RemoteConfig(NacosClient(BootstrapSettings()), DATA_ID)
    _reload = os.environ.get("GATEWAY_RELOAD", "").lower() in ("1", "true", "yes")
    _config = uvicorn.Config(
        "gateway.main:app",
        host=_cfg.get("http.host", "0.0.0.0"),
        port=int(_cfg.get("http.port", 0)),
        reload=_reload,
        # 信任哪些来源的 X-Forwarded-For / X-Real-IP。不配的话 uvicorn 只信
        # 127.0.0.1,上层网关转过来的请求会被当成"不可信代理",真实客户端 IP 被丢掉,
        # request.client.host 和访问日志里看到的都是代理自己的地址。
        # 只在进程启动时读一次(uvicorn.Config 构造后就定了),改了要重启网关。
        forwarded_allow_ips=_cfg.get("http.forwarded_allow_ips", "127.0.0.1"),
        # 关掉 uvicorn 自带的访问日志:AccessLogMiddleware 已经记了一条,
        # 而且带请求 ID 和业务 code,uvicorn 那条只会重复刷屏
        access_log=False,
    )
    _server = uvicorn.Server(_config)
    _sock = _config.bind_socket()
    _port = _sock.getsockname()[1]
    os.environ["GATEWAY_BOUND_PORT"] = str(_port)
    # 容器健康检查读这个文件拿端口(随机端口写不进 compose),
    # 跟 gRPC 服务的 GRPC_PORT_FILE 是同一个套路
    import pathlib as _pathlib
    _pathlib.Path(os.environ.get("GATEWAY_PORT_FILE", "/tmp/gateway.port")).write_text(str(_port))

    if _reload:
        # --reload 会起子进程,socket 通过 fd 继承过去;环境变量也一并继承
        ChangeReload(_config, target=_server.run, sockets=[_sock]).run()
    else:
        _server.run(sockets=[_sock])
