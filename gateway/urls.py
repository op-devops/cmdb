"""路由注册表:所有 URL 前缀集中在这里,加接口不用动 main.py。

前缀带模块名(/auth、/users 之类)有两个用处:
  1. /docs 里按模块分组,路径自解释
  2. 上层网关(APISIX 等)可以直接按前缀路由到本项目,不用配 proxy-rewrite 剥前缀

健康检查故意不加前缀:探活路径要稳定、好配,而且 docker-compose 的 healthcheck
和上层负载均衡都按 /healthz 写死。
"""

from fastapi import FastAPI
from gateway.routers import health, hosts, categories

# (router, 前缀)。加一个 router 就在这里加一行。
ROUTES: list[tuple] = [
    (health.router, ""),
    (hosts.router, "/hosts"),
    (categories.router, "/categories"),
]


def register(app: FastAPI) -> None:
    for router, prefix in ROUTES:
        app.include_router(router, prefix=prefix)
