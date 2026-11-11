"""内部 gRPC 调用的共享令牌鉴权。

东西向调用不该谁都能发:gRPC 端口虽然只在内网、端口还是随机的,但只要有人能
连上就能把 VerifyToken / GetUser 全调一遍。这里用一个共享令牌做最低限度的门禁。

令牌放在 Nacos 的独立 data-id(cmdb-security.yaml)里,网关和服务
都从同一处读,轮换时改一个地方两边都热更新。token 通过 metadata 传(相当于
HTTP header),不进 proto,加减不影响契约。

注意这是"防误调/防内网乱窜",不是密码学意义的强认证——insecure_channel 上
令牌是明文的。要防窃听得上 mTLS,那是另一个量级的工程。
"""

import logging
from typing import Callable

import grpc

logger = logging.getLogger(__name__)

# metadata 的 key 必须全小写(gRPC 规范:HTTP/2 header 名不允许大写)
TOKEN_KEY = "x-internal-token"

# 免鉴权的方法前缀由 Nacos 配置(grpc.exempt_methods)提供。这里只留一个兜底值:
# 健康检查不带令牌(Docker healthcheck 和 scripts/grpc_healthcheck.py 都不带),
# 拦了它容器会一直 unhealthy。配置漏写不该把容器搞挂,所以保底放行它。
# 配置里显式写成空列表([])则表示"一个都不放行",会被尊重。
DEFAULT_EXEMPT_METHODS = ("/grpc.health.v1.Health/",)


class TokenClientInterceptor(
    grpc.UnaryUnaryClientInterceptor,
    grpc.UnaryStreamClientInterceptor,
    grpc.StreamUnaryClientInterceptor,
    grpc.StreamStreamClientInterceptor,
):
    """给出去的每个调用自动带上令牌。

    token_provider 是个可调用对象而不是字符串:每次调用现取,
    Nacos 上轮换令牌后不用重启进程。
    """

    def __init__(self, token_provider: Callable[[], str]):
        self._token = token_provider

    def _with_token(self, details):
        token = self._token()
        if not token:
            return details
        md = list(details.metadata or [])
        md.append((TOKEN_KEY, token))
        return details._replace(metadata=md)

    def intercept_unary_unary(self, continuation, details, request):
        return continuation(self._with_token(details), request)

    def intercept_unary_stream(self, continuation, details, request):
        return continuation(self._with_token(details), request)

    def intercept_stream_unary(self, continuation, details, request_iterator):
        return continuation(self._with_token(details), request_iterator)

    def intercept_stream_stream(self, continuation, details, request_iterator):
        return continuation(self._with_token(details), request_iterator)


class TokenServerInterceptor(grpc.ServerInterceptor):
    """校验每个进来的调用是否带了正确的令牌。

    两个 provider 都是可调用对象而不是取好的值:每次调用现取,
    Nacos 上改令牌或改免鉴权名单后不用重启进程。
    """

    def __init__(
        self,
        token_provider: Callable[[], str],
        exempt_provider: Callable[[], list[str] | None] | None = None,
    ):
        self._token = token_provider
        self._exempt = exempt_provider or (lambda: None)
        self._deny = grpc.unary_unary_rpc_method_handler(
            lambda request, context: context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "缺少或错误的内部调用令牌"
            )
        )

    def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method
        exempt = self._exempt()
        if exempt is None:              # 配置里没这一项 -> 用兜底值
            exempt = DEFAULT_EXEMPT_METHODS
        if any(method.startswith(prefix) for prefix in exempt):
            return continuation(handler_call_details)

        expected = self._token()
        if not expected:                      # 没配令牌 = 不启用鉴权
            return continuation(handler_call_details)

        md = dict(handler_call_details.invocation_metadata or ())
        if md.get(TOKEN_KEY) != expected:
            logger.warning("拒绝无令牌调用: %s", method)
            return self._deny
        return continuation(handler_call_details)
