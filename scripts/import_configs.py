"""把 nacos-configs/*.yaml 批量导入 Nacos 配置中心(data-id = 文件名)。

用法: python3 dev.py import-configs(或 PYTHONPATH=.:gen python3 scripts/import_configs.py)
连接参数取自 NACOS_* 环境变量 / .env,与服务本身一致。
"""

import pathlib
import sys
import time

import httpx

from libs.common.config import BootstrapSettings

CONFIG_DIR = pathlib.Path(__file__).resolve().parent.parent / "nacos-configs"

# Nacos 是共享的长期实例,起容器时不能用 depends_on 等它健康,这里自己重试
WAIT_SECONDS = 90
RETRY_INTERVAL = 3


def wait_for_nacos(http: httpx.Client, base: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        try:
            r = http.get(f"{base}/v1/console/health/readiness", timeout=3)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = type(e).__name__
        print(f"等待 Nacos 就绪({last})…")
        time.sleep(RETRY_INTERVAL)
    sys.exit(f"Nacos {WAIT_SECONDS}s 内没有就绪({last}),共享中间件栈起了吗?")


def main() -> None:
    s = BootstrapSettings()
    base = f"http://{s.server}/nacos"
    with httpx.Client(timeout=10) as http:
        wait_for_nacos(http, base)
        token = ""
        r = http.post(
            f"{base}/v1/auth/login",
            data={"username": s.username, "password": s.password},
        )
        if r.status_code == 200:
            token = r.json().get("accessToken", "")
        elif r.status_code != 404:  # 404 = 未开启鉴权
            sys.exit(f"Nacos 登录失败: {r.status_code} {r.text}")

        for f in sorted(CONFIG_DIR.glob("*.yaml")):
            params = {
                "dataId": f.name,
                "group": s.group,
                "content": f.read_text(),
                "type": "yaml",
            }
            if s.namespace:
                params["tenant"] = s.namespace
            if token:
                params["accessToken"] = token
            r = http.post(f"{base}/v1/cs/configs", data=params)
            if r.status_code == 200 and r.text.strip() == "true":
                print(f"✔ {f.name}")
            else:
                sys.exit(f"✘ {f.name}: {r.status_code} {r.text}")


if __name__ == "__main__":
    main()
