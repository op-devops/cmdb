from typing import Any, Optional

from pydantic import BaseModel


class Envelope(BaseModel):
    """统一响应外壳:HTTP 状态码固定 200,真实结果由 code 区分(0 = 成功)。"""

    code: int = 0
    message: str = "success"
    data: Optional[Any] = None


def json_response(data: Any = None, message: str = "success") -> Envelope:
    return Envelope(code=0, message=message, data=data)
