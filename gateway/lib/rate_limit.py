"""网关侧限速:Redis 固定窗口,做成 FastAPI 依赖挂在需要的路由上。

    @router.get("/captcha", dependencies=[Depends(RateLimit("captcha", times=5, seconds=60))])

计数放 Redis 而不是进程内存:网关横向扩容(--scale gateway=3)时,进程内计数会让
每个副本各算各的,实际额度变成 N 倍。
"""

import logging

import redis
from fastapi import HTTPException, Request

logger = logging.getLogger("gateway")

_client: redis.Redis | None = None


def init_redis(host: str, port: int, password: str, db: int = 0) -> None:
    """建客户端并立刻 ping 一次。

    redis.Redis(...) 只建连接池,不真正连接,不 ping 的话 Redis 挂着也看不出来。
    网关这边 Redis 只用来限速,而限速本身是 fail-open 的,所以连不上只告警、不阻止启动。
    """
    global _client
    _client = redis.Redis(
        host=host, port=port, password=password, db=db, decode_responses=True,
        # 不设超时的话,地址被防火墙丢包时 ping 和每次限速计数都会一直卡住
        socket_connect_timeout=3, socket_timeout=3,
    )
    try:
        _client.ping()
    except redis.RedisError as e:
        logger.warning("Redis 连接失败 %s:%s/%s: %s —— 限速将失效(放行所有请求)", host, port, db, e)


def client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("redis 还没初始化,先调用 init_redis(...)")
    return _client


class RateLimit:
    """按客户端 IP 的固定窗口限速。

    固定窗口不是滑动窗口:跨窗口边界最坏能挤进 2 倍额度。防刷够用,
    要精确配额得改 ZSET 滑动窗口,复杂度高一个量级。
    """

    def __init__(self, name: str, times: int, seconds: int):
        self._name = name
        self._times = times
        self._seconds = seconds

    def __call__(self, request: Request) -> None:
        if self._times <= 0:          # 配 0 表示不限速
            return
        ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{self._name}:{ip}"
        try:
            pipe = client().pipeline()
            pipe.incr(key)
            # nx=True:只在还没有 TTL 时才设。每次都无条件 EXPIRE 会不断续期,
            # 窗口永远滚不掉,等于一直请求就一直被封。
            pipe.expire(key, self._seconds, nx=True)
            used = int(pipe.execute()[0])
        except redis.RedisError as e:
            # 限速器自己挂了不该把接口一起带下去,放行并告警
            logger.error("限速器不可用,本次放行: %s", e)
            return
        if used > self._times:
            raise HTTPException(
                status_code=429,
                detail=f"{self._name} 请求过于频繁:{self._seconds} 秒内最多 {self._times} 次",
            )
