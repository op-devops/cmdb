#!/usr/bin/env python3
"""直连 cmdb-service 的 gRPC 客户端,绕过网关直接测服务。

为什么单独搞一个:网关那条链路(HTTP -> APISIX -> 网关 -> gRPC)任何一环出问题都会
盖住服务本身的行为。QA 客户端直接连 gRPC,能把"服务对不对"和"网关配没配好"分开。
加了新 RPC 想快速试一下,也不用先把前端和网关都接完。

连接和令牌全自动:走 Nacos 服务发现找 cmdb-service,令牌从 Nacos 的 cmdb-security.yaml
读,Nacos 地址/命名空间来自 .env——跟服务/网关同一套。

用法:改 main() 里要调的那行,注释掉其它的,然后在 cmdb 项目根目录执行:
    python qa/client.py
"""

import json
import os
import pathlib
import sys
import uuid

# QA 客户端只连内网服务(Nacos、gRPC 实例),绝不该走代理。
# 环境里若设了 http_proxy,Nacos SDK 和 gRPC 都会尝试经代理连,而代理到不了
# nacos:8848 / 10.100.2.x,报 "All server are not available"。这里直接清掉。
# 必须在 import grpc / nacos 之前设 GRPC_ENABLE_HTTP_PROXY,channel 建立时才读得到。
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)
os.environ["GRPC_ENABLE_HTTP_PROXY"] = "0"

# 让脚本在项目根目录直接 `python qa/client.py` 就能跑,不用手动设 PYTHONPATH
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "gen")]

import grpc  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402

from cmdb.v1 import cmdb_pb2, cmdb_pb2_grpc  # noqa: E402
from libs.common.grpc_auth import TokenClientInterceptor  # noqa: E402
from libs.common.request_id import RequestIdClientInterceptor, set_request_id  # noqa: E402

SERVICE = "cmdb-service"
SECURITY_DATA_ID = "cmdb-security.yaml"
OPERATOR = "qa-client"          # 写操作会记进服务日志的 operator_id


def make_stub():
    """走 Nacos 服务发现连 cmdb-service,令牌从 cmdb-security.yaml 读。返回 (stub, closer)。"""
    from libs.common.config import BootstrapSettings
    from libs.common.grpc_resolver import NacosChannelPool
    from libs.common.nacos_client import NacosClient, RemoteConfig

    client = NacosClient(BootstrapSettings())
    token = RemoteConfig(client, SECURITY_DATA_ID).get("grpc.token", "")
    interceptors = [TokenClientInterceptor(lambda: token), RequestIdClientInterceptor()]
    pool = NacosChannelPool(client, SERVICE, interceptors=interceptors)
    return cmdb_pb2_grpc.CmdbServiceStub(pool.channel()), pool.close


def _dump(msg) -> None:
    print(json.dumps(MessageToDict(msg, preserving_proto_field_name=True,
                                   always_print_fields_with_no_presence=True),
                     ensure_ascii=False, indent=2))


# ---------- 各 RPC 的简单封装 ----------

def ping(stub, message="hi"):
    _dump(stub.Ping(cmdb_pb2.PingRequest(message=message), timeout=5))


def list_hosts(stub, keyword="", page=1, page_size=20):
    _dump(stub.ListHosts(cmdb_pb2.ListHostsRequest(
        keyword=keyword, page=page, page_size=page_size), timeout=5))


def get_host(stub, host_id):
    _dump(stub.GetHost(cmdb_pb2.GetHostRequest(id=host_id), timeout=5))


def create_host(stub, name, hostname, username, port=22, pkey="", desc=""):
    _dump(stub.CreateHost(cmdb_pb2.CreateHostRequest(
        name=name, hostname=hostname, port=port, username=username,
        pkey=pkey, desc=desc, operator_id=OPERATOR), timeout=5))


def delete_host(stub, host_id):
    _dump(stub.DeleteHost(cmdb_pb2.DeleteHostRequest(id=host_id, operator_id=OPERATOR), timeout=5))


def verify_host(stub, host_id, password=""):
    # 验证要真的 SSH 连主机,可能十几秒,给个长超时
    _dump(stub.VerifyHost(cmdb_pb2.VerifyHostRequest(
        id=host_id, password=password, operator_id=OPERATOR), timeout=30))

def get_shell_cred(stub, host_id):
    _dump(stub.GetShellCredential(cmdb_pb2.GetShellCredentialRequest(id=host_id), timeout=5))



def main() -> None:
    # 这次调用打个 qa- 前缀的请求 ID,服务日志里 grep qa- 就能找到本次调用
    set_request_id(f"qa-{uuid.uuid4().hex[:8]}")
    stub, closer = make_stub()
    try:
        # 要测哪个就放开哪行,其它注释掉 ↓↓↓
        # ping(stub)
        # list_hosts(stub, keyword="")
        # get_host(stub, "453k6g4vdm5dfc72vgk97hq92p")
        # create_host(stub, name="kali", hostname="192.168.11.20", username="ren", desc="test")
        # verify_host(stub, "453h3vak3q4vgh5wmru4d97k9j", password="910202.")
        # delete_host(stub, "主机ID")
        get_shell_cred(stub=stub,host_id="453h3vak3q4vgh5wmru4d97k9j")
    except grpc.RpcError as e:
        sys.exit(f"gRPC 调用失败: {e.code().name} - {e.details()}")
    finally:
        closer()


if __name__ == "__main__":
    main()
