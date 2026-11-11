"""请求 ID:同一条请求在网关和各 gRPC 服务的日志里用同一个 ID 串起来。

ID 只由上层网关(APISIX 的 request-id 插件)生成,**本进程不自己造**:
自己生成的话,同一条链路上可能冒出两个 ID(网关一个、服务一个),
或者绕过 APISIX 的请求也带着个看起来正常的 ID,日志反而串不起来。
拿不到就记成 "-",日志里一眼能看出这条请求没走 APISIX。
传递方式:HTTP 用 X-Request-Id 头,gRPC 用 metadata 的 x-request-id
(gRPC 规范要求 metadata 的 key 全小写;HTTP 头大小写不敏感,所以两边可以共用一个常量)。

这不是链路追踪:没有 span、没有耗时分解、没有调用树,只解决"把散在各处的日志按请求串起来"。
要看调用链得上 OpenTelemetry,那是另一套部署。
"""

import logging
from contextvars import ContextVar
from typing import Callable

import grpc

# HTTP 头名 / gRPC metadata key,两边同名
REQUEST_ID_KEY = "x-request-id"

# 没有 ID 时的占位:启动日志、健康检查、绕过 APISIX 直连的请求都是这个值
UNSET = "-"

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default=UNSET)

# 外部传进来的值不可信,限制长度和字符集,避免把脏东西写进日志(日志注入)
_MAX_LEN = 64
_SAFE = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_.")


def get_request_id() -> str:
    return _REQUEST_ID.get()


def set_request_id(value: str | None) -> str:
    """只接受上游传来的 ID,不生成。值缺失或不合法都记成 UNSET。返回最终生效的值。"""
    raw = (value or "").strip()[:_MAX_LEN]
    rid = raw if raw and all(c in _SAFE for c in raw) else UNSET
    _REQUEST_ID.set(rid)
    return rid


class RequestIdFilter(logging.Filter):
    """给每条日志补上 request_id 字段,日志格式里才能用 %(request_id)s。

    挂在 handler 上而不是 logger 上:handler 是所有日志的必经之路,
    第三方库自己创建的 logger 也能带上。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def install_log_filter() -> None:
    """在 logging.basicConfig(...) 之后调用一次。"""
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())


class RequestIdClientInterceptor(
    grpc.UnaryUnaryClientInterceptor,
    grpc.UnaryStreamClientInterceptor,
    grpc.StreamUnaryClientInterceptor,
    grpc.StreamStreamClientInterceptor,
):
    """出站调用自动带上当前请求的 ID,调用点不用管。"""

    def _with_id(self, details):
        md = list(details.metadata or [])
        md.append((REQUEST_ID_KEY, get_request_id()))
        return details._replace(metadata=md)

    def intercept_unary_unary(self, continuation, details, request):
        return continuation(self._with_id(details), request)

    def intercept_unary_stream(self, continuation, details, request):
        return continuation(self._with_id(details), request)

    def intercept_stream_unary(self, continuation, details, request_iterator):
        return continuation(self._with_id(details), request_iterator)

    def intercept_stream_stream(self, continuation, details, request_iterator):
        return continuation(self._with_id(details), request_iterator)


class RequestIdServerInterceptor(grpc.ServerInterceptor):
    """把调用方带来的请求 ID 放进 contextvar,本次调用的日志就都带上它。

    注意不能直接在 intercept_service 里 set:那是在 gRPC 的分发线程里执行的,
    业务方法跑在线程池的另一个线程上,contextvar 不会跟过去。所以要包一层,
    在业务方法真正执行的那个线程里 set。
    """

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.request_streaming or handler.response_streaming:
            # 方法不存在,或是流式调用(本项目没有);流式要包的是迭代器,语义不同,先不处理
            return handler
        md = dict(handler_call_details.invocation_metadata or ())
        original: Callable = handler.unary_unary

        def behavior(request, context):
            set_request_id(md.get(REQUEST_ID_KEY))
            return original(request, context)

        return grpc.unary_unary_rpc_method_handler(
            behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
