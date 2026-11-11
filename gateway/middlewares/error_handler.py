import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from gateway.schemas.envelope import Envelope

logger = logging.getLogger("gateway")


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """兜底捕获未处理异常;HTTPException 等已知业务错误已被框架处理成正常响应,不会走到这里。"""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("未处理异常: %s %s", request.method, request.url.path)
            request.state.envelope_code = 500
            # 这里必须直接返回真正的 Response;json_response()/Envelope 是给路由函数用的,
            # 路由函数返回值靠 FastAPI 自动转换成响应,但 middleware.dispatch() 没有这层自动转换。
            envelope = Envelope(code=500, message="服务器开小差了")
            return JSONResponse(status_code=200, content=envelope.model_dump())
