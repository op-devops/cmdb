"""主机分类的请求体。响应统一走 Envelope。"""

from typing import Annotated
from pydantic import BaseModel, Field, StringConstraints


class CategoryCreate(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)
    ] = Field(..., description="分类名称,1~30 个字符,全局唯一")
