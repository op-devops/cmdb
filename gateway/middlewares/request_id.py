"""把上层网关(APISIX)传来的 X-Request-Id 接进来,并原样写回响应头。

ID 一律由 APISIX 生成,网关不自己造:自己造会让同一条链路出现两个 ID,
绕过 APISIX 的请求也会带上个看着正常的 ID,反而更难排查。
没有 ID 时日志里记 "-",正好说明这条请求没走 APISIX(本机直连调试就是这种)。

写回响应头有两个用处:
  1. 用户报错时截图或复制这个 ID,能直接定位到日志
  2. 前端可以在错误提示里显示它
"""

from starlette.middleware.base import BaseHTTPMiddleware

from libs.common.request_id import REQUEST_ID_KEY, UNSET, set_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = set_request_id(request.headers.get(REQUEST_ID_KEY))
        response = await call_next(request)
        if request_id != UNSET:
            # 没有 ID 就不写这个头,免得客户端拿到一个没意义的 "-"
            response.headers[REQUEST_ID_KEY] = request_id
        return response
