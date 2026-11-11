"""把 HTTPException / 参数校验错误统一转成 Envelope,HTTP 状态码固定 200。

未处理的异常不走这里,由 gateway.middlewares.error_handler.ErrorHandlerMiddleware 兜底。

message 直接用 exc.detail:业务错误("已存在的主机名称【xxx】")要能让用户看到,
跟 auth 网关的做法一致。参数校验错误翻成中文后再回。
"""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from gateway.lib.validation_msg import summarize
from gateway.schemas.envelope import Envelope

logger = logging.getLogger(__name__)

_MESSAGES: dict[int, str] = {
    400: "请求有误",
    401: "认证失败",
    403: "没有权限",
    404: "资源不存在",
    405: "请求方法不允许",
    408: "请求超时",
    409: "操作冲突,请重试",
    413: "请求内容过大",
    415: "不支持的内容类型",
    422: "请求参数不合法",
    429: "请求过于频繁,请稍后再试",
    500: "服务器开小差了",
    502: "服务器开小差了",
    503: "服务器开小差了",
    504: "服务器开小差了",
}


def message_for(status_code: int) -> str:
    """没单独列的状态码按 4xx / 5xx 归类兜底,不会漏出 None。"""
    if status_code in _MESSAGES:
        return _MESSAGES[status_code]
    return "请求有误" if status_code < 500 else "服务器开小差了"


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # 给访问日志用:Envelope 约定下 HTTP 状态码恒为 200,只有这里知道真实的业务 code
    request.state.envelope_code = exc.status_code
    # 覆盖业务代码手写的 HTTPException,也兜住框架自己抛的(路由不存在、方法不允许等)
    logger.error(
        "%s %s %s %s",
        request.method, request.url.path, exc.status_code, str(exc.detail),
    )
    envelope = Envelope(code=exc.status_code, message=str(exc.detail))
    return JSONResponse(status_code=200, content=envelope.model_dump())


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 去掉 input:那是用户提交的原始值,密码之类的会明文进日志。
    # 字段名、类型、约束都还在,排查够用了
    errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    logger.error(
        "RequestValidationError %s %s -> detail=%s",
        request.method, request.url.path, str(errors),
    )
    # 日志里留 pydantic 原文,回给客户端的是翻好的中文
    request.state.envelope_code = 422
    envelope = Envelope(code=422, message=summarize(errors))
    return JSONResponse(status_code=200, content=envelope.model_dump())
