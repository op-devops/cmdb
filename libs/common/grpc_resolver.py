"""基于 Nacos 服务发现的 gRPC channel 池。

按健康实例维护长连接 channel,按 Nacos 实例权重加权随机取用,
后台线程定时刷新实例列表(新实例建连、下线实例关闭、权重变更生效)。
"""

import logging
import random
import threading

import grpc

from libs.common.nacos_client import NacosClient

logger = logging.getLogger(__name__)


class NoInstanceError(RuntimeError):
    """Nacos 中没有该服务的健康实例。"""


class NacosChannelPool:
    def __init__(
        self,
        client: NacosClient,
        service_name: str,
        refresh_interval: float = 10.0,
        interceptors: list[grpc.UnaryUnaryClientInterceptor] | None = None,
    ):
        self._client = client
        self._service = service_name
        self._interval = refresh_interval
        # 建 channel 时统一包一层(鉴权令牌、trace 注入之类),调用点无感
        self._interceptors = interceptors or []
        self._lock = threading.Lock()
        self._channels: dict[str, grpc.Channel] = {}
        self._instances: list[tuple[str, float]] = []  # (addr, weight)
        self._stop = threading.Event()
        self._refresh()
        threading.Thread(
            target=self._refresh_loop, daemon=True, name=f"grpc-resolver-{service_name}"
        ).start()

    def channel(self) -> grpc.Channel:
        """按 Nacos 实例权重加权随机选一个实例的 channel。"""
        with self._lock:
            if not self._instances:
                raise NoInstanceError(f"Nacos 中没有 {self._service} 的健康实例")
            addrs = [addr for addr, _ in self._instances]
            weights = [w for _, w in self._instances]
            if sum(weights) <= 0:
                weights = None  # 权重全为 0 时退化为均等随机
            addr = random.choices(addrs, weights=weights, k=1)[0]
            return self._channels[addr]

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            for ch in self._channels.values():
                ch.close()
            self._channels.clear()
            self._instances = []

    def _refresh(self) -> None:
        try:
            instances = sorted(self._client.healthy_instances(self._service))
        except Exception:
            logger.warning("刷新 %s 实例列表失败,沿用旧列表", self._service, exc_info=True)
            return
        with self._lock:
            if instances != self._instances:
                logger.info("%s 实例变更(addr, weight): %s -> %s",
                            self._service, self._instances, instances)
            addrs = {addr for addr, _ in instances}
            for addr in addrs:
                if addr not in self._channels:
                    ch = grpc.insecure_channel(addr)
                    if self._interceptors:
                        ch = grpc.intercept_channel(ch, *self._interceptors)
                    self._channels[addr] = ch
            for addr in set(self._channels) - addrs:
                self._channels.pop(addr).close()
            self._instances = instances

    def _refresh_loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._refresh()
