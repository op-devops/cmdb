"""鉴权依赖:从 Authorization 头拿 token,调 auth-service 的 VerifyToken 换出当前用户。

cmdb 自己不签发也不验签 token——登录在 auth 项目,这里只是把 token 原样交过去。
权限码(host.host.view 之类)也由 auth 算好下发,这里只比对。

    # 只要登录
    def endpoint(user: CurrentUser = Depends(current_user)): ...

    # 还要求权限码
    @router.get("", dependencies=[Depends(RequirePerm("host.host.view"))])
"""

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.v1 import auth_pb2, auth_pb2_grpc

from gateway.lib.grpc_client import grpc_errors, timeout

# 让 /docs 出现 Authorize 按钮。auto_error=False:缺 token 时由下面统一抛 401,
# 才能走 exception_handlers 的 Envelope 映射
bearer = HTTPBearer(auto_error=False, description="auth 登录拿到的 access_token")


@dataclass
class CurrentUser:
    id: str
    username: str
    nickname: str
    roles: list[str] = field(default_factory=list)
    perms: list[str] = field(default_factory=list)
    is_supper: bool = False
    host_category_all: bool = False
    allowed_categories: list[str] = field(default_factory=list)

    def has_perm(self, code: str) -> bool:
        return self.is_supper or code in self.perms


def current_user(request: Request,creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> CurrentUser:
    token = (creds.credentials if creds else "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    stub = auth_pb2_grpc.AuthServiceStub(request.app.state.auth_pool.channel())
    with grpc_errors("auth-service"):
        resp = stub.VerifyToken(
            auth_pb2.VerifyTokenRequest(
                token=token,
                # auth 会拿这两个跟签发时的值比对,防止 token 被盗用。
                # 这里的 IP 必须是真实客户端 IP:cmdb-gateway.yaml 的 forwarded_allow_ips
                # 要跟 auth-gateway.yaml 配成一样,否则同一个 token 在两边看到的 IP 不同,会被判冒用
                client_ip=request.client.host if request.client else "",
                user_agent=request.headers.get("user-agent", ""),
            ),
            timeout=timeout(request),
        )
    return CurrentUser(
        id=resp.user_id, username=resp.username, nickname=resp.nickname,
        roles=list(resp.roles), perms=list(resp.perms), is_supper=resp.is_supper,
        host_category_all=resp.host_category_all or resp.is_supper,
        allowed_categories=list(resp.allowed_categories),
    )


class RequirePerm:
    """按权限码放行,挂在路由的 dependencies 上。超管绕过所有检查。"""

    def __init__(self, code: str):
        self._code = code

    def __call__(self, user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.has_perm(self._code):
            raise HTTPException(status_code=403, detail="无权限访问")
        return user
