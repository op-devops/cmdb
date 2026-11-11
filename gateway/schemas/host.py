"""主机管理的请求体。响应统一走 Envelope,data 里是 protobuf 转出来的 dict。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# 名称、地址、账号前后带空格基本都是复制粘贴带进来的,直接去掉再校验长度。
# 私钥不能这么处理:PEM 内容原样保存,只在服务端判断是不是全空白。
#
# 字符串长度用 min_length / max_length。ge / le 是数值比较,用在字符串上
# pydantic 会拿字符串去跟数字比,直接抛 TypeError,请求变成 500。
# 长度限制写在别名上,新建和编辑共用同一套。
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=20)]
Hostname = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=30)]
Desc = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
CategoryId = Annotated[str, StringConstraints(strip_whitespace=True, max_length=26)]

PKEY_DESC = "独立私钥(PEM)。留空 = 使用平台全局密钥"


class HostCreate(BaseModel):
    # /docs 里"Example Value"和"Try it out"默认填的就是这个
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "name": "web-01",
                "hostname": "192.168.1.10",
                "port": 22,
                "username": "root",
                "pkey": "",
                "desc": "官网前端",
                "category_id": "",
            }
        ]
    })

    name: Name = Field(..., description="主机名称,全局唯一,3~20 个字符")
    hostname: Hostname = Field(..., description="主机名或 IP,3~50 个字符")
    port: int = Field(22, ge=1, le=65535, description="SSH 端口")
    username: Username = Field(..., description="SSH 登录用户名,3~30 个字符")
    pkey: str = Field("", max_length=16384, description=PKEY_DESC)
    desc: Desc = Field("", description="备注")
    category_id: CategoryId = Field("", description="所属分类ID,留空=未分类")

class HostUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "name": "web-01",
                "hostname": "192.168.1.10",
                "port": 2222,
                "username": "root",
                "desc": "官网前端,SSH 端口已改",
                "category_id": "",
            }
        ]
    })

    name: Name = Field(..., description="主机名称,全局唯一,3~20 个字符")
    hostname: Hostname = Field(..., description="主机名或 IP,3~50 个字符")
    port: int = Field(22, ge=1, le=65535, description="SSH 端口")
    username: Username = Field(..., description="SSH 登录用户名,3~30 个字符")
    # 私钥不会回给前端,编辑表单里没有原值,所以:
    #   不传 / null -> 保持原样
    #   ""          -> 清掉独立密钥,改用全局密钥
    #   非空        -> 替换
    pkey: str | None = Field(None, max_length=16384, description="不传表示不修改;传空串表示清除")
    desc: Desc = Field("", description="备注")
    category_id: CategoryId = Field("", description="所属分类ID,留空=未分类")

class HostVerify(BaseModel):
    # 首次验证才需要:用它登录后把平台公钥推到主机上,用完即丢、不保存。
    # 主机已配独立密钥、或已能用全局密钥登录时,留空即可。
    password: str = Field("", max_length=128, description="首次验证的登录密码,不保存")