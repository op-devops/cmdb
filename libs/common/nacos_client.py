"""Nacos 封装:配置中心(加载 + 热更新)与服务注册发现。

基于 nacos-sdk-python 0.1.x(HTTP API,连 8848 端口)。

心跳没有用 SDK 托管:SDK 每次心跳都带全量 beat JSON(含注册时的 weight),
会把控制台在线调整的实例权重覆盖回去。这里改发"轻心跳"(不带实例信息,
与 Java 官方客户端 lightBeatEnabled 行为一致),权重只在注册时上报一次,
之后完全由 Nacos 侧维护,控制台改权重才能生效。
"""

import json
import logging
import socket
import threading
from typing import Any, Callable

import nacos
import yaml

from libs.common.config import BootstrapSettings

logger = logging.getLogger(__name__)
logging.getLogger("nacos").setLevel(logging.WARNING)

_HEARTBEAT_INTERVAL = 5.0


def local_ip() -> str:
    """探测本机对外 IP(UDP connect 不真正发包)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class NacosClient:
    def __init__(self, settings: BootstrapSettings | None = None):
        self.settings = settings or BootstrapSettings()
        self._group = self.settings.group
        self._client = nacos.NacosClient(
            server_addresses=self.settings.server,
            namespace=self.settings.namespace or None,
            username=self.settings.username,
            password=self.settings.password,
        )

    # ---------- 配置中心 ----------

    def load_config(self, data_id: str) -> dict:
        content = self._client.get_config(data_id, self._group, no_snapshot=True)
        if content is None:
            raise RuntimeError(
                f"Nacos 中不存在配置 data-id={data_id} group={self._group},"
                f"请先导入 nacos-configs/(python3 dev.py import-configs)"
            )
        return yaml.safe_load(content) or {}

    def watch_config(self, data_id: str, on_change: Callable[[dict], None]) -> None:
        """配置变更时以解析后的 dict 回调 on_change。"""

        def _cb(args: dict) -> None:
            raw = args.get("raw_content") or args.get("content") or ""
            try:
                on_change(yaml.safe_load(raw) or {})
            except Exception:
                logger.exception("处理配置变更失败 data_id=%s", data_id)

        self._client.add_config_watcher(data_id, self._group, _cb)

    # ---------- 注册发现 ----------

    def register(
        self, service_name: str, ip: str, port: int,
        weight: float = 1.0, metadata: dict | None = None,
    ) -> None:
        """注册临时实例并起轻心跳线程(停止心跳即被 Nacos 摘除)。

        weight 为初始权重,之后可在 Nacos 控制台在线调整,调用方刷新实例列表时生效。
        """
        self._client.add_naming_instance(
            service_name, ip, port,
            weight=weight, metadata=metadata or {}, ephemeral=True, group_name=self._group,
        )
        self._beat_stop = threading.Event()
        threading.Thread(
            target=self._light_beat_loop,
            args=(service_name, ip, port, weight, metadata or {}),
            daemon=True,
            name=f"nacos-beat-{service_name}",
        ).start()
        logger.info("已注册到 Nacos: %s -> %s:%s (weight=%s)", service_name, ip, port, weight)

    def deregister(self, service_name: str, ip: str, port: int) -> None:
        """停心跳并摘除实例。"""
        beat_stop = getattr(self, "_beat_stop", None)
        if beat_stop is not None:
            beat_stop.set()
        try:
            self._client.remove_naming_instance(
                service_name, ip, port, ephemeral=True, group_name=self._group
            )
            logger.info("已从 Nacos 摘除: %s -> %s:%s", service_name, ip, port)
        except Exception:
            logger.exception("Nacos 摘除失败(临时实例心跳停止后也会被自动剔除)")

    def healthy_instances(self, service_name: str) -> list[tuple[str, float]]:
        """返回健康实例的 ("ip:port", weight) 列表。"""
        resp = self._client.list_naming_instance(
            service_name, group_name=self._group, healthy_only=True
        )
        hosts = resp.get("hosts") or []
        return [
            (f"{h['ip']}:{h['port']}", float(h.get("weight", 1.0)))
            for h in hosts
            if h.get("enabled", True)
        ]

    def _light_beat_loop(
        self, service_name: str, ip: str, port: int, weight: float, metadata: dict
    ) -> None:
        """轻心跳:只报 ip:port 不带实例信息,避免覆盖控制台在线改的权重。

        Nacos 返回 20404(实例不存在,如 Nacos 重启丢了临时实例)时带全量信息重新注册。
        """
        params = {
            "serviceName": f"{self._group}@@{service_name}",
            "ip": ip,
            "port": port,
            "groupName": self._group,
        }
        if self.settings.namespace:
            params["namespaceId"] = self.settings.namespace
        while not self._beat_stop.wait(_HEARTBEAT_INTERVAL):
            try:
                resp = self._client._do_sync_req(
                    "/nacos/v1/ns/instance/beat", None, dict(params), None,
                    self._client.default_timeout, "PUT", "naming",
                )
                code = json.loads(resp.read()).get("code")
                if code == 20404:
                    logger.warning("Nacos 中实例已不存在,重新注册 %s -> %s:%s", service_name, ip, port)
                    self._client.add_naming_instance(
                        service_name, ip, port,
                        weight=weight, metadata=metadata, ephemeral=True, group_name=self._group,
                    )
            except Exception:
                logger.warning("Nacos 心跳失败,%ss 后重试", _HEARTBEAT_INTERVAL, exc_info=True)


class RemoteConfig:
    """挂在 Nacos 配置中心上的一份 YAML 配置:启动加载,变更热更新。

    get 支持 "http.port" 这样的点路径;on_change 注册变更回调。
    """

    def __init__(self, client: NacosClient, data_id: str):
        self._data_id = data_id
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        self._data = client.load_config(data_id)
        client.watch_config(data_id, self._apply)
        logger.info("已从 Nacos 加载配置 %s: keys=%s", data_id, list(self._data))

    @property
    def data(self) -> dict:
        with self._lock:
            return self._data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def on_change(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def _apply(self, data: dict) -> None:
        with self._lock:
            self._data = data
        logger.info("配置已热更新 %s: keys=%s", self._data_id, list(data))
        for fn in self._listeners:
            try:
                fn(data)
            except Exception:
                logger.exception("配置变更回调失败 %s", self._data_id)
