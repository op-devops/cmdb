"""gRPC 服务端访问日志:每个调用一条,成功失败都记。

日志里的 [request_id] 由 libs.common.request_id 的过滤器注入,所以网关那条 HTTP
访问日志和这里的 gRPC 访问日志能按同一个 ID 串起来。
"""

import logging
import time

import grpc

from libs.common.request_id import REQUEST_ID_KEY, set_request_id

logger = logging.getLogger("access")


def _status_of(context) -> str:
    """abort 之后 context.code() 是设置的状态码;正常返回是 None。"""
    code = context.code()
    return code.name if code is not None else "OK"


class AccessLogServerInterceptor(grpc.ServerInterceptor):
    """放在拦截器链的最前面(但在请求 ID 之后):被令牌校验拒掉的调用也要记一条。"""

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        method = handler_call_details.method
        if handler is None:
            # 方法没注册,gRPC 会回 UNIMPLEMENTED。默认静默,这里补一条日志——
            # 多半是调用方和服务端契约版本对不上(stub 有、服务端没重启)。
            # 请求 ID 在分发线程里取,直接从 metadata 拿并塞进 contextvar,好让日志带上它。
            md = dict(handler_call_details.invocation_metadata or ())
            set_request_id(md.get(REQUEST_ID_KEY))
            logger.warning("%s UNIMPLEMENTED 方法未注册", method)
            return None
        if handler.request_streaming or handler.response_streaming:
            return handler
        original = handler.unary_unary

        def behavior(request, context):
            start = time.perf_counter()
            try:
                response = original(request, context)
            except Exception as e:
                cost = (time.perf_counter() - start) * 1000
                # abort() 抛的是普通 Exception,状态码要从 context 上取;
                # 业务代码漏抓的异常没有状态码,客户端会收到 UNKNOWN
                status = _status_of(context)
                detail = context.details()
                if isinstance(detail, bytes):
                    detail = detail.decode("utf-8", "replace")
                if status == "OK":
                    status = f"UNKNOWN({type(e).__name__})"
                logger.warning("%s %s %.1fms %s", method, status, cost, detail or "")
                raise
            cost = (time.perf_counter() - start) * 1000
            logger.info("%s %s %.1fms", method, _status_of(context), cost)
            return response

        return grpc.unary_unary_rpc_method_handler(
            behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
