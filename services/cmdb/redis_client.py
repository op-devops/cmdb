"""Redis 客户端:连接参数来自 Nacos(cmdb-service.yaml 的 redis.*),在 server 启动时初始化一次。"""

import json
from typing import Any

import redis

_client: redis.Redis | None = None


def init_redis(host: str, port: int, password: str, db: int = 0) -> None:
    """建客户端并立刻 ping 一次。

    redis.Redis(...) 只建连接池,不真正连接——不 ping 的话 Redis 挂着服务也能"正常启动",
    要等第一个请求进来才报错。session / 验证码全靠 Redis,连不上就直接启动失败。
    """
    global _client
    _client = redis.Redis(
        host=host, port=port, password=password, db=db, decode_responses=True,
        # 不设超时的话,地址被防火墙丢包时 ping 会一直卡住,既不报错也不启动
        socket_connect_timeout=3, socket_timeout=3,
    )
    try:
        _client.ping()
    except redis.RedisError as e:
        raise RuntimeError(f"Redis 连接失败 {host}:{port}/{db}: {e}") from e


def client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("redis 还没初始化,先调用 init_redis(...)")
    return _client


def create_session(token: str, data: dict[str, Any], ttl_seconds: int) -> None:
    client().set(f"session:{token}", json.dumps(data), ex=ttl_seconds)


def get_session(token: str) -> dict[str, Any] | None:
    raw = client().get(f"session:{token}")
    return json.loads(raw) if raw else None


def delete_session(token: str) -> None:
    client().delete(f"session:{token}")


def save_captcha(captcha_id: str, code: str, ttl_seconds: int) -> None:
    client().set(f"captcha:{captcha_id}", code, ex=ttl_seconds)


def pop_captcha(captcha_id: str) -> str | None:
    """取出并立即删除——验证码一次性,验错了也要重新取,免得拿同一张图暴力试密码。"""
    key = f"captcha:{captcha_id}"
    pipe = client().pipeline()
    pipe.get(key)
    pipe.delete(key)
    return pipe.execute()[0]
