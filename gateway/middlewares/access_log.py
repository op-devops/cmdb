"""网关访问日志:每个请求一条,成功失败都记。

自己记而不是用 uvicorn 自带的访问日志,有三个原因:
  1. uvicorn 的格式固定,带不上请求 ID
  2. Envelope 约定下 HTTP 状态码恒为 200,只看状态码分不出成功失败,
     要把 body 里的 code 也记上(由异常处理器写进 request.state)
  3. 能记真实客户端 IP 和耗时

所以 gateway/main.py 里把 uvicorn 的 access_log 关掉了,避免一个请求两条日志。
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        cost = (time.perf_counter() - start) * 1000
        # 业务 code 由异常处理器塞进 request.state(见 exception_handlers.py);
        # 没有就是走通了,按 0 记
        code = getattr(request.state, "envelope_code", 0)
        query = f"?{request.url.query}" if request.url.query else ""
        client = request.client.host if request.client else "-"
        line = "%s %s %s%s %s code=%s %.1fms"
        args = (client, request.method, request.url.path, query,
                response.status_code, code, cost)
        # 业务失败(code != 0)按 warning 记,方便只看异常流量
        (logger.warning if code else logger.info)(line, *args)
        return response
