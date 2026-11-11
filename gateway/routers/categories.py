"""主机分类。权限码 host.category.*,真相源在 cmdb-service。"""

from fastapi import APIRouter, Depends, Path, Request

from cmdb.v1 import cmdb_pb2, cmdb_pb2_grpc

from gateway.lib.deps import CurrentUser, RequirePerm
from gateway.lib.grpc_client import grpc_errors, timeout, to_dict
from gateway.schemas.category import CategoryCreate
from gateway.schemas.envelope import Envelope, json_response

router = APIRouter(tags=["主机分类"])

UPSTREAM = "cmdb-service"


# 前缀 /categories 在 gateway/urls.py 里加
@router.get("", response_model=Envelope,
            dependencies=[Depends(RequirePerm("host.category.view"))])
def list_categories(request: Request):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
        resp = stub.ListCategories(cmdb_pb2.ListCategoriesRequest(), timeout=timeout(request))
    return json_response(to_dict(resp))

@router.post("", response_model=Envelope)
def create_category(body: CategoryCreate, request: Request,
              user: CurrentUser = Depends(RequirePerm("host.category.add"))):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
      resp = stub.CreateCategory(
          cmdb_pb2.CreateCategoryRequest(name=body.name, operator_id=user.id),
          timeout=timeout(request),
      )
    return json_response(to_dict(resp.category))

@router.delete("/{category_id}", response_model=Envelope)
def delete_category(request: Request,
              category_id: str = Path(..., min_length=26, max_length=26, description="分类 ID"),
              user: CurrentUser = Depends(RequirePerm("host.category.del"))):
    stub = cmdb_pb2_grpc.CmdbServiceStub(request.app.state.cmdb_pool.channel())
    with grpc_errors(UPSTREAM):
      stub.DeleteCategory(
          cmdb_pb2.DeleteCategoryRequest(id=category_id, operator_id=user.id),
          timeout=timeout(request),
      )
    return json_response()
