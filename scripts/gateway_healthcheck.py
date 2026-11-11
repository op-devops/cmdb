"""容器健康检查:探网关的 /healthz。

端口从 GATEWAY_PORT_FILE(默认 /tmp/gateway.port,由 gateway.main 启动时写入)读取
——网关用随机端口,容器里没法写死。跟 grpc_healthcheck.py 是同一个套路。

用标准库而不是 curl:镜像里没装 curl/wget。
"""

import os
import pathlib
import sys
import urllib.request


def main() -> int:
    try:
        port = pathlib.Path(
            os.environ.get("GATEWAY_PORT_FILE", "/tmp/gateway.port")
        ).read_text().strip()
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
        return 0 if resp.status == 200 else 1
    except Exception as e:
        print(f"healthcheck failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
