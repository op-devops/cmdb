"""网关调 gRPC 的公共件:超时、错误码映射、protobuf 转 dict。

没有它的话每个端点都要重复写一遍 try/except NoInstanceError/RpcError,
而且各写各的状态码映射,很快就不一致了。Stub 在各端点里自己建——
它只是 channel 上几个方法的绑定,造价接近于零,真正贵的 channel 在 NacosChannelPool 里长期复用。
"""

from contextlib import contextmanager

import grpc
from fastapi import HTTPException, Request
from google.protobuf.json_format import MessageToDict

from libs.common.grpc_resolver import NoInstanceError

# gRPC 状态码 -> HTTP 状态码。没列到的一律 502:
# 那些是服务端异常或传输层问题,属于"上游坏了",不该让客户端以为是自己请求有问题。
_STATUS_MAP = {
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.FAILED_PRECONDITION: 400,
    grpc.StatusCode.UNAUTHENTICATED: 401,
    grpc.StatusCode.PERMISSION_DENIED: 403,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.ALREADY_EXISTS: 409,
    grpc.StatusCode.RESOURCE_EXHAUSTED: 429,
    grpc.StatusCode.UNAVAILABLE: 503,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
}


def timeout(request: Request) -> float:
    return request.app.state.cfg.get("grpc.timeout_seconds", 3)


@contextmanager
def grpc_errors(upstream: str = "上游服务"):
    """把 gRPC 异常翻成 HTTPException,交给 exception_handlers 统一转 Envelope。"""
    try:
        yield
    except NoInstanceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except grpc.RpcError as e:
        code = _STATUS_MAP.get(e.code())
        if code is None:
            # UNKNOWN 往往是服务端抛了未捕获异常,details 里才有真正原因
            raise HTTPException(status_code=502, detail=f"{upstream} 调用失败: {e.code().name}")
        raise HTTPException(status_code=code, detail=e.details())


def to_dict(message) -> dict:
    """protobuf -> dict,字段名保持 snake_case,默认值也输出。

    always_print_fields_with_no_presence:proto3 里 is_verified=False 和"没设置"
    没法区分,不开这个标志的话 False/0/"" 会被整个省掉,前端拿不到字段。
    """
    return MessageToDict(
        message, preserving_proto_field_name=True, always_print_fields_with_no_presence=True
    )
