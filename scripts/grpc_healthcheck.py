"""容器 healthcheck:按 grpc.health.v1 标准协议探测本机 gRPC 服务。

端口从 GRPC_PORT_FILE(默认 /tmp/grpc.port,由 server 启动时写入)读取,
兼容随机端口。SERVING 退出 0,其余情况退出 1。
"""

import os
import pathlib
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def main() -> int:
    try:
        port = pathlib.Path(os.environ.get("GRPC_PORT_FILE", "/tmp/grpc.port")).read_text().strip()
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            resp = health_pb2_grpc.HealthStub(channel).Check(
                health_pb2.HealthCheckRequest(service=""), timeout=2
            )
        return 0 if resp.status == health_pb2.HealthCheckResponse.SERVING else 1
    except Exception as e:
        print(f"healthcheck failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
