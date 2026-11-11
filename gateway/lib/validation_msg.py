"""把 pydantic 的校验错误翻成中文。

pydantic v2 的报错是给开发者看的英文结构体(`String should have at least 8 characters`),
直接回给用户既看不懂也不该看。这里按错误类型(error["type"])映射成中文模板,
用 ctx 里的参数填空。

不用 pydantic-i18n 之类的库:那要多一个依赖,而且它翻的是通用文案,
字段名还是英文的 —— 而"密码"比"password"有用得多。
"""

from typing import Any

# 字段名 -> 中文标签。没列到的字段直接用原名。
FIELD_LABELS: dict[str, str] = {
    "name": "主机名称",
    "hostname": "主机地址",
    "port": "SSH 端口",
    "username": "登录用户名",
    "pkey": "独立密钥",
    "desc": "备注",
    "host_id": "主机ID",
    "page": "页码",
    "page_size": "每页条数",
    "keyword": "关键字",
}

# pydantic 错误类型 -> 中文模板。{} 里的名字是 ctx 里的键。
_TYPE_MESSAGES: dict[str, str] = {
    "missing": "不能为空",
    "string_type": "必须是文本",
    "string_too_short": "长度不能少于 {min_length} 个字符",
    "string_too_long": "长度不能超过 {max_length} 个字符",
    "string_pattern_mismatch": "格式不正确",
    "int_type": "必须是整数",
    "int_parsing": "必须是整数",
    "float_type": "必须是数字",
    "bool_type": "必须是真/假",
    "bool_parsing": "必须是真/假",
    "list_type": "必须是列表",
    "dict_type": "必须是对象",
    "greater_than": "必须大于 {gt}",
    "greater_than_equal": "不能小于 {ge}",
    "less_than": "必须小于 {lt}",
    "less_than_equal": "不能大于 {le}",
    "too_short": "至少要有 {min_length} 项",
    "too_long": "最多只能有 {max_length} 项",
    "enum": "只能是 {expected} 之一",
    "json_invalid": "请求体不是合法的 JSON",
    "value_error": "取值不合法",
}


def _label(loc: tuple) -> str:
    """从错误位置取出字段名。loc 形如 ('body', 'password') 或 ('query', 'page')。"""
    # 跳过 body/query/path 这类来源标记,取最后一个字符串片段
    parts = [p for p in loc if isinstance(p, str) and p not in ("body", "query", "path", "header")]
    if not parts:
        return "请求参数"
    name = parts[-1]
    return FIELD_LABELS.get(name, name)


def to_chinese(errors: list[dict[str, Any]]) -> list[str]:
    """[{'type': 'string_too_short', 'loc': ('body','password'), ...}]
    -> ['密码长度不能少于 8 个字符']"""
    out: list[str] = []
    for err in errors:
        label = _label(err.get("loc") or ())
        template = _TYPE_MESSAGES.get(err.get("type", ""))
        # 去掉首尾空白后再要求至少 1 个字符,用户看到的应该是"不能为空"
        if err.get("type") == "string_too_short" and (err.get("ctx") or {}).get("min_length") == 1:
            template = "不能为空"
        if template is None:
            # 没覆盖到的类型:退回 pydantic 原文,至少不丢信息
            detail = err.get("msg", "不合法")
        else:
            try:
                detail = template.format(**(err.get("ctx") or {}))
            except (KeyError, IndexError):
                detail = template
        out.append(f"{label}{detail}")
    return out


def summarize(errors: list[dict[str, Any]]) -> str:
    """多个错误拼成一句话,最多列 3 条免得太长。"""
    msgs = to_chinese(errors)
    if not msgs:
        return "请求参数不合法"
    if len(msgs) <= 3:
        return "；".join(msgs)
    return "；".join(msgs[:3]) + f"（另有 {len(msgs) - 3} 项）"
