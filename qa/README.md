# qa/ — 直连 gRPC 的测试客户端

绕过网关(HTTP → APISIX → 网关 → gRPC)直接连 cmdb-service,用来把"服务本身对不对"
和"网关配没配好"分开排查。加了新 RPC 想快速试,也不用等前端和网关接完。

## 前提

服务跑起来并注册到了 Nacos(本机进程或容器都行):

```bash
./dev.py service        # 另开一个终端
```

## 用法

在 **cmdb 项目根目录**执行(脚本自己会把 `.:gen` 加进 import 路径):

```bash
python qa/client.py ping
python qa/client.py list-hosts --keyword web
python qa/client.py get-host <主机ID>
python qa/client.py create-host --name web-01 --hostname 192.168.1.10 --username root
python qa/client.py verify-host <主机ID> --password '登录密码'
python qa/client.py delete-host <主机ID>
```

## 连接与令牌:全自动

- **找服务**:走 Nacos 服务发现,跟网关找 cmdb-service 的方式一样。
- **令牌**:从 Nacos 的 `cmdb-security.yaml` 读。
- **Nacos 地址/命名空间**:来自 `.env`(`NACOS_SERVER` / `NACOS_NAMESPACE`)。

都不用在命令行传。脚本会自动清掉 `http_proxy`——内网服务不走代理,否则 Nacos
和 gRPC 都会连不上。

## 日志关联

每次调用会带一个 `qa-` 开头的请求 ID。服务日志里 `grep qa-` 就能找到本次调用的全过程:

```
[qa-a1b2c3d4] /cmdb.v1.CmdbService/VerifyHost OK 8231.4ms
```
