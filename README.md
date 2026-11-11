# cmdb

服务器管理基础模块

对外 FastAPI 网关(南北向唯一入口),对内 gRPC(cmdb-service),Nacos 做服务发现 + 配置中心。
**所有运行配置放在 Nacos 配置中心**,服务只靠 `NACOS_*` 环境变量引导。

## 结构

```
cmdb/
├── protos/                     # 契约(唯一真相源)
│   └── cmdb/v1/cmdb.proto
├── gen/                        # 生成的 stub(不手改,已 gitignore)
├── vendor/                     # 依赖别人的契约(从 contracts 同步,只读,进版本库)
├── libs/common/                # 共享基建
│   ├── config.py               #   引导配置(仅 NACOS_* 来自环境)
│   ├── nacos_client.py         #   配置中心 + 注册发现封装(轻心跳)
│   └── grpc_resolver.py        #   基于 Nacos 的 gRPC channel 池(刷新 + 加权随机)
├── gateway/                    # 对外 FastAPI:routers / schemas / middlewares
├── services/
│   └── cmdb/
│       ├── server.py           #   gRPC 服务本体(随机端口 + 注册 Nacos)
│       ├── db/                 #   SQLAlchemy model + 手写 join 查询
│       └── alembic/            #   表结构由 DBA 手动迁移,不接自动化流水线
├── nacos-configs/              # 需导入 Nacos 的配置样例(data-id = 文件名)
├── scripts/                    # 导配置 / gRPC 健康检查 / 建库账号
├── docker-compose.yml          # 本地 Nacos + 依赖组件 + 业务容器
└── dev.py                      # 开发任务入口(替代 Makefile)
```

## 快速开始(Docker,推荐)

Nacos / 数据库 / Redis 是**整台机器共享的一套**,不由本项目启动。先确认它在跑:

```bash
cd ../infra && docker compose up -d && cd -   # 共享中间件栈,同时会创建 devops-net 网络
```

再起本项目:

```bash
cp .env.example .env
python3 dev.py check             # 探测共享中间件端口是否可达
python3 dev.py db-init           # 在共享库上建 cmdb 库 + DML 账号 cmdb_app(每个新项目一次)
python3 dev.py up                # 只起本项目的容器
python3 dev.py db-upgrade        # DBA 手动建表(alembic,DDL 权限账号)

# 网关是随机端口、不发布到宿主机,经 APISIX 访问(路由前缀 /cmdb-api,先下发 APISIX 路由)
curl --noproxy '*' 'http://127.0.0.1:9080/cmdb-api/ping?message=hi'

docker compose up -d --scale cmdb=3   # 横向扩容:每容器独立 IP + 随机端口注册
python3 dev.py down
```

## 快速开始(本地进程,开发调试)

```bash
cp .env.example .env
python3 dev.py deps
python3 dev.py check             # 共享中间件要先起(infra 目录),控制台 http://127.0.0.1:8848/nacos
python3 dev.py import-configs    # 把 nacos-configs/*.yaml 导入配置中心
python3 dev.py proto-sync        # 从 contracts 同步依赖的契约到 vendor/
python3 dev.py proto             # 生成 gRPC stub 到 gen/
python3 dev.py db-upgrade        # 建表(DDL 账号,DBA 手动跑)

python3 dev.py service   # 终端 1:gRPC 服务,自动注册到 Nacos
python3 dev.py gateway           # 终端 2:FastAPI,从 Nacos 发现 cmdb-service

curl --noproxy '*' http://127.0.0.1:$(cat /tmp/gateway.port)/healthz
```

> 本地进程跑服务时,Nacos 里 `cmdb-service.yaml` 里的连接地址要从 docker 服务名改成
> `127.0.0.1`,端口以共享中间件栈实际发布到宿主机的为准(见 infra 目录的 README)。

## 配置约定

- **引导配置**(环境变量,仅这些):`NACOS_SERVER` `NACOS_NAMESPACE` `NACOS_GROUP`
  `NACOS_USERNAME` `NACOS_PASSWORD`,见 `.env.example`。
- **运行配置**(端口、上游、DSN、日志级别……)全部在 Nacos,data-id 见 `nacos-configs/`。
- 配置支持热更新:改 Nacos 里的配置会通过 watcher 推给服务。
  例外:`db.dsn` 和 `http.thread_pool_size` 只在启动时读一次,改了要重启进程。
- **数据库账号权限分离**:应用运行时用的是 `db.dsn` 里只有 DML 权限的账号;
  建表/改表(DDL)必须用 `CMDB_MIGRATOR_DATABASE_URL`(`.env`,DBA 专用)手动跑 `python3 dev.py db-upgrade`,
  不接自动化流水线,也不给应用进程 DDL 权限——见 `scripts/db/init.sql`。

## 共享中间件的约定

中间件全机器只有一套,在 `../infra` 里统一起停;本项目的 `docker-compose.yml` 只声明加入
外部网络 `devops-net`,不再定义这些组件。

- 容器内按服务名互访:`nacos:8848`。
- 网关是随机端口,注册到 Nacos 的服务名是 `cmdb-gateway`。APISIX 等上层网关
  用 Nacos 服务发现找它(注意指定命名空间),不要在路由里写死地址。
- 网络不存在时 `python3 dev.py up` 会直接报 network not found,先去 infra 目录把栈起来。
- 数据库同样共享,容器内用 `mysql:3306`。共享实例的 initdb 脚本只在数据卷
  首次初始化时跑,**本项目的库和账号不会自动创建**,所以有一次性的 `python3 dev.py db-init`
  (幂等,可重复跑)。
- 各项目之间要错开:本项目用库 `cmdb`、DML 账号 `cmdb_app`。
- Redis 也是共享的(容器内 `redis:6379`),靠 db 编号隔离,本项目用 db `0`。

## 改契约的流程

改 `protos/**` + 改调用方,**一个 commit 搞定**:

```bash
python3 dev.py lint           # buf lint,规范检查
python3 dev.py breaking       # buf breaking,破坏性变更检测
python3 dev.py proto          # 重新生成 stub
```

## 共享契约仓库

契约分三层,别搞混谁能改:

| | 是什么 | 谁改 | 进版本库 |
|---|---|---|---|
| `protos/` | **本项目契约的源** | 本项目 | 是 |
| `../contracts` | 发布/分发点,下游从这里取 | `dev.py proto-publish` | 是(独立仓库) |
| `vendor/` | 别人契约的只读副本 | `dev.py proto-sync` | 是 |
| `gen/` | 编出来的 stub | `dev.py proto` | 否 |

改完自己的 `protos/` 要**发布一次**,下游才同步得到:

```bash
python3 dev.py lint           # 发布前先查:破坏性变更会连累所有下游
python3 dev.py breaking
python3 dev.py proto-publish  # protos/ -> contracts,会先列出改动再让你确认
```

发布是幂等的,没变化就什么都不做。发完记得去契约仓库提交。

## 依赖别人的契约

共享的是 `.proto` 而不是 `gen/`:stub 的 gencode 版本必须和本项目 `requirements.txt`
里的 protobuf 运行时同源,各自生成才不会因为上游升级 grpcio 而炸。

```bash
python3 dev.py proto-sync     # 同步依赖契约(改完 contracts 后跑)
python3 dev.py proto-check    # 校验 vendor 与 contracts 一致(放 CI)
python3 dev.py proto          # 重新生成 stub
```

要改依赖的契约,**去它自己的项目里改 `protos/` 再 `proto-publish`**,然后这边
`proto-sync` 取回来——直接改 `vendor/`(或直接改 contracts)会被 `proto-check` 拦下。别人契约的 lint 和破坏性变更检测也在契约仓库里做,
本项目的 `buf.yaml` 只管 `protos/`。

调用方拿到 stub 之后,还要在 Nacos 配置的 `upstreams` 段里加上游服务名,
运行时用 `NacosChannelPool(client, "<对方>-service")` 建一个 channel 池——
跟本项目自己那个 pool 是同一套用法。

## 服务注册与负载均衡

- 服务以 **临时实例 + 随机端口** 注册到 Nacos,进程退出 / 心跳停止即被摘除。
- 心跳走"轻心跳"(不带实例信息),所以在 Nacos 控制台在线调整的权重不会被心跳覆盖回去。
- 调用方(`NacosChannelPool`)每 10s 刷新健康实例列表,按权重加权随机选 channel;
  无健康实例时抛 `NoInstanceError`,网关转成 HTTP 503。
- 容器 healthcheck 走标准 `grpc.health.v1` 协议(`scripts/grpc_healthcheck.py`),
  退出时先置 `NOT_SERVING`、再从 Nacos 摘实例,最后优雅停机。
