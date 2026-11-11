"""主机管理。迁自老运维平台 apps/host/views.py 的 HostView,权限码 host.host.* 跟老平台一致。

跟老接口的区别:
  - 新建和编辑拆成 POST / PUT(老接口都走 POST,靠有没有 id 区分)
  - 列表分页,支持关键字搜索(老接口一次返回全部)
  - 不回传私钥 pkey,只给 has_pkey
暂未迁移:分组、扩展信息、保存前的 SSH 验证、按分组的数据权限、删除前的引用检查、导入。
"""

from fastapi import APIRouter, Depends, Path, Query, Request

from cmdb.v1 import cmdb_pb2, cmdb_pb2_grpc

from gateway.lib.deps import CurrentUser, RequirePerm
from gateway.lib.grpc_client import grpc_errors, timeout, to_dict
from gateway.schemas.envelope import Envelope, json_response
from gateway.schemas.host import HostCreate, HostUpdate, HostVerify

router = APIRouter(tags=["主机管理"])

UPSTREAM = "cmdb-service"


def _host_id(host_id: str = Path(..., min_length=26, max_length=26, description="主机 ID")) -> str:
    return host_id

def _scope(user: CurrentUser) -> cmdb_pb2.DataScope:
  return cmdb_pb2.DataScope(
      host_category_all=user.host_category_all,
      allowed_categories=user.allowed_categories,
  )



# 前缀 /hosts 在 gateway/urls.py 里加,这里只写资源路径
@router.get("", response_model=Envelope)
def list_hosts(
    request: Request,
    user: CurrentUser = Depends(RequirePerm("host.host.view")),
    keyword: str = Query("", max_length=50, description="同时匹配名称、地址、备注"),
    category_id: str = Query("", max_length=26, description="分类筛选:空=全部,none=未分类,其余=分类ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
        resp = stub.ListHosts(
            cmdb_pb2.ListHostsRequest(keyword=keyword, category_id=category_id, page=page, page_size=page_size,scope=_scope(user)),
            timeout=timeout(request),
        )
    return json_response(to_dict(resp))


@router.get("/{host_id}", response_model=Envelope)
def get_host(request: Request, host_id: str = Depends(_host_id),user: CurrentUser = Depends(RequirePerm("host.host.view"))):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
        resp = stub.GetHost(cmdb_pb2.GetHostRequest(id=host_id,scope=_scope(user)), timeout=timeout(request))
    return json_response(to_dict(resp.host))


@router.post("", response_model=Envelope)
def create_host(body: HostCreate, request: Request,
                user: CurrentUser = Depends(RequirePerm("host.host.add"))):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
        resp = stub.CreateHost(
            cmdb_pb2.CreateHostRequest(
                name=body.name, hostname=body.hostname, port=body.port,
                username=body.username, pkey=body.pkey, desc=body.desc,
                category_id=body.category_id, operator_id=user.id,scope=_scope(user)
            ),
            timeout=timeout(request),
        )
    return json_response(to_dict(resp.host))


@router.put("/{host_id}", response_model=Envelope)
def update_host(body: HostUpdate, request: Request, host_id: str = Depends(_host_id),
                user: CurrentUser = Depends(RequirePerm("host.host.edit"))):
    req = cmdb_pb2.UpdateHostRequest(
        id=host_id, name=body.name, hostname=body.hostname, port=body.port,
        username=body.username, desc=body.desc, category_id=body.category_id, operator_id=user.id,scope=_scope(user)
    )
    # optional 字段:不赋值就是"没传",服务端据此保持原密钥不变
    if body.pkey is not None:
        req.pkey = body.pkey
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
        resp = stub.UpdateHost(req, timeout=timeout(request))
    return json_response(to_dict(resp.host))


@router.delete("/{host_id}", response_model=Envelope)
def delete_host(request: Request, host_id: str = Depends(_host_id),
                user: CurrentUser = Depends(RequirePerm("host.host.del"))):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
        stub.DeleteHost(
            cmdb_pb2.DeleteHostRequest(id=host_id, operator_id=user.id,scope=_scope(user)),
            timeout=timeout(request),
        )
    return json_response()

@router.post("/{host_id}/verify",response_model=Envelope)
def verify_host(body:HostVerify, request:Request,host_id: str = Depends(_host_id),
                user:CurrentUser = Depends(RequirePerm("host.host.add"))):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    # 验证要真的 SSH 连主机,可能十几秒,单独给个长超时,别用默认那 3 秒
    with grpc_errors(UPSTREAM):
        resp = stub.VerifyHost(cmdb_pb2.VerifyHostRequest(id=host_id,password=body.password,operator_id=user.id,scope=_scope(user)), timeout=30)
        return json_response(to_dict(resp))