#!/usr/bin/env python3
"""开发任务入口:python3 dev.py <任务>

不用 make:所有子进程都用当前解释器(sys.executable)跑,不依赖 PATH 上有
python / uvicorn / alembic,也就不会因为环境里只有 python3 没有 python 而挂掉。

    python3 dev.py              # 列出所有任务
    python3 dev.py proto
"""

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
GEN = ROOT / "gen"
PY = sys.executable
# 共享契约仓库。三层关系:
#   protos/   本项目契约的源,只有本项目能改
#   contracts 发布/分发点,proto-publish 把 protos/ 推上去,别人从这里取
#   vendor/   别人契约的只读副本,proto-sync 取回来,进版本库(构建期不必访问 contracts)
CONTRACTS = (ROOT / "../contracts").resolve()
VENDOR = ROOT / "vendor"
PROTO_DEPS = ['auth/v1']

TASKS: dict[str, tuple[str, object]] = {}


def task(name: str, help_text: str):
    def deco(fn):
        TASKS[name] = (help_text, fn)
        return fn

    return deco


# 本机跑内网服务时,shell 里的 http_proxy 会让 Nacos SDK / gRPC 尝试经代理连内网,
# 报 "All server are not available"。这些进程只连内网,统一关掉代理。
# 容器不经 dev.py 启动(走 Dockerfile CMD),所以生产不受影响。
_NO_PROXY = {
    "no_proxy": "*", "NO_PROXY": "*",
    "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "",
    "GRPC_ENABLE_HTTP_PROXY": "0",
}


def run(*args: object, env: dict | None = None) -> None:
    """跑一条命令,失败就带原样退出码退出。"""
    argv = [str(a) for a in args]
    # flush:子进程直接写 fd,不 flush 的话回显会排在子进程输出后面
    print(f"$ {' '.join(argv)}", flush=True)
    child_env = dict(os.environ)
    # gen/ 里的 stub 用绝对 import(from cmdb.v1 import ...),
    # 需要 gen 与仓库根都在 PYTHONPATH 上
    child_env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(GEN)])
    if env:
        child_env.update(env)
    rc = subprocess.call(argv, cwd=ROOT, env=child_env)
    if rc != 0:
        sys.exit(rc)


def confirm(question: str) -> bool:
    """非 TTY(CI/管道)下直接放行——能跑到这一步说明是显式调用的。"""
    if not sys.stdin.isatty():
        return True
    return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")


def load_dotenv() -> dict[str, str]:
    """把 env 文件读成字典(只给需要 DDL 账号的迁移任务用)。

    默认读 .env;跑生产迁移时用 ENV_FILE 指过去:
        ENV_FILE=.env.prod python3 dev.py db-upgrade
    跟 NACOS_NAMESPACE=prod python3 dev.py import-configs 是同一个套路——
    本机进程靠环境变量切环境,容器靠 docker-compose.yml 固定在 prod。
    """
    name = os.environ.get("ENV_FILE", ".env")
    path = ROOT / name
    if not path.is_file():
        sys.exit(f"缺少 {name},先执行:cp .env.example {name}")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def split_hostport(value: str, default_port: int = 8848) -> tuple[str, int]:
    host, _, port = value.partition(":")
    return host or "127.0.0.1", int(port or default_port)


def compose_running(service: str) -> bool:
    """本项目的某个 compose 服务是否有容器在跑。"""
    if shutil.which("docker") is None:
        return False
    out = subprocess.run(
        ["docker", "compose", "ps", "-q", service],
        cwd=ROOT, capture_output=True, text=True,
    )
    return out.returncode == 0 and bool(out.stdout.strip())


def need(binary: str) -> None:
    if shutil.which(binary) is None:
        sys.exit(f"找不到 {binary},请先安装")


# ---------- 依赖与契约 ----------


@task("deps", "安装 requirements.txt")
def deps() -> None:
    run(PY, "-m", "pip", "install", "-r", "requirements.txt")


@task("proto-publish", "把本项目的 protos/ 发布到 contracts(下游才同步得到)")
def proto_publish() -> None:
    """protos/ 是源,contracts 是分发点。改完自己的契约要发布一次,
    下游项目才能 proto-sync 到新版。"""
    if not CONTRACTS.is_dir():
        sys.exit(f"找不到契约仓库 {CONTRACTS},先建/clone 它,或改 dev.py 里的 CONTRACTS")
    changes = []
    for src in sorted((ROOT / "protos").rglob("*.proto")):
        rel = src.relative_to(ROOT / "protos")
        dst = CONTRACTS / rel
        if not dst.is_file():
            changes.append(("新增", rel, src, dst))
        elif dst.read_bytes() != src.read_bytes():
            changes.append(("更新", rel, src, dst))
    if not changes:
        print("契约仓库已是最新,无需发布")
        return
    for kind, rel, _, _ in changes:
        print(f"  {kind}  {rel}")
    print("提示:发布前先跑 python3 dev.py lint / breaking,破坏性变更会连累所有下游")
    if not confirm(f"发布到 {CONTRACTS}?"):
        sys.exit("已取消")
    for _, _, src, dst in changes:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    print(f"已发布 {len(changes)} 个文件,记得去 contracts 仓库提交;"
          f"下游跑 python3 dev.py proto-sync 取新版")


def _dep_pairs() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """[(contracts 里的源目录, vendor 里的目标目录)]。"""
    return [(CONTRACTS / dep, VENDOR / dep) for dep in PROTO_DEPS]


@task("proto-sync", "从 contracts 同步依赖的 .proto 到 vendor/")
def proto_sync() -> None:
    if not CONTRACTS.is_dir():
        sys.exit(f"找不到契约仓库 {CONTRACTS},先 clone 它,或改 dev.py 里的 CONTRACTS")
    for src, dst in _dep_pairs():
        if not src.is_dir():
            sys.exit(f"契约仓库里没有 {src.relative_to(CONTRACTS)}/")
        files = sorted(src.glob("*.proto"))
        if not files:
            sys.exit(f"{src} 下没有 .proto 文件")
        # 先清空再拷,这样上游删掉的文件不会在 vendor 里留下孤儿
        shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dst / f.name)
            print(f"  {dst.relative_to(ROOT)}/{f.name}")
    print("同步完成,记得把 vendor/ 的变更一起提交")


@task("proto-check", "校验 vendor/ 与 contracts 一致(CI 用,不一致则失败)")
def proto_check() -> None:
    """vendor/ 是只读副本:要改依赖的契约,去 contracts 改完再 proto-sync。
    这一步防的是有人图省事直接改 vendor,导致两边悄悄分叉。"""
    if not CONTRACTS.is_dir():
        sys.exit(f"找不到契约仓库 {CONTRACTS}")
    drift = []
    for src, dst in _dep_pairs():
        names = {f.name for f in src.glob("*.proto")} | {f.name for f in dst.glob("*.proto")}
        for name in sorted(names):
            a, b = src / name, dst / name
            if not a.is_file():
                drift.append(f"{dst.relative_to(ROOT)}/{name}: contracts 里已删除")
            elif not b.is_file():
                drift.append(f"{dst.relative_to(ROOT)}/{name}: vendor 里缺失")
            elif a.read_bytes() != b.read_bytes():
                drift.append(f"{dst.relative_to(ROOT)}/{name}: 内容与 contracts 不一致")
    if drift:
        for d in drift:
            print(f"  ✗ {d}")
        sys.exit("vendor/ 与 contracts 已分叉,执行 python3 dev.py proto-sync")
    print(f"✓ {len(PROTO_DEPS)} 个依赖与 contracts 一致")


@task("proto", "用 grpcio-tools 生成 gRPC stub 到 gen/")
def proto() -> None:
    protos = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "protos").rglob("*.proto"))
    if not protos:
        sys.exit("protos/ 下没有 .proto 文件")
    includes = ["-I", "protos"]
    # vendor 单独给一个 -I:它不属于本项目的契约,buf.yaml 也不管它
    vendored = sorted(str(p.relative_to(ROOT)) for p in VENDOR.rglob("*.proto"))
    if not vendored:
        sys.exit("vendor/ 是空的,先执行:python3 dev.py proto-sync")
    includes += ["-I", "vendor"]
    protos += vendored
    GEN.mkdir(exist_ok=True)
    # grpcio-tools 自带 protoc 与插件,不联网,gencode 与 protobuf 运行时天然同版本
    # (Dockerfile 构建期用的是同一条命令)
    run(PY, "-m", "grpc_tools.protoc", *includes,
        "--python_out=gen", "--pyi_out=gen", "--grpc_python_out=gen", *protos)


@task("lint", "buf lint:契约规范检查")
def lint() -> None:
    need("buf")
    run("buf", "lint")


@task("breaking", "buf breaking:对比 main 分支做破坏性变更检测")
def breaking() -> None:
    need("buf")
    run("buf", "breaking", "--against", ".git#branch=main")


# ---------- 依赖组件 ----------


@task("check", "探测共享中间件端口是否可达(宿主机视角)")
def check() -> None:
    import socket

    dotenv = load_dotenv()
    # 按服务名探测,跟 .env 和 Nacos 配置里的写法一致;本机要在 /etc/hosts 里能解析
    targets = [("Nacos", *split_hostport(dotenv.get("NACOS_SERVER", "nacos:8848")))]
    targets.append(("mysql", "mysql", int(dotenv.get("INFRA_DB_PORT", 3306))))
    # 共享 Redis 的宿主端口可能被本机原生 redis 挤开(容器内始终是 6379),
    # 实际值写在 .env 的 INFRA_REDIS_PORT,以 infra 目录的 .env 为准
    targets.append(("Redis", "redis", int(dotenv.get("INFRA_REDIS_PORT", 6379))))
    bad = 0
    for label, host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=2):
                ok, why = True, ""
        except socket.gaierror:
            ok, why = False, f"  <- 解析不了 {host},在 /etc/hosts 里加: 127.0.0.1  {host}"
        except OSError:
            ok, why = False, ""
        print(f"  {'✓' if ok else '✗'} {label:<10} {host}:{port}{why}")
        bad += not ok
    if bad:
        sys.exit(f"\n{bad} 个组件连不上。共享中间件栈起了吗?到 infra 目录执行 docker compose up -d")
    print("\n共享中间件可达(✓ 只说明端口有人监听,认不出是不是共享栈那一份)")


@task("nacos-namespace", "在 Nacos 建命名空间(按环境隔离配置,幂等)")
def nacos_namespace() -> None:
    """用法: python3 dev.py nacos-namespace prod

    命名空间要先存在,配置才能推进去并在控制台看得到。
    填进 .env 的 NACOS_NAMESPACE 是命名空间的 **ID**,不是名字——
    这里直接把 ID 和名字设成一样,免得两者对不上。
    """
    import httpx

    from libs.common.config import BootstrapSettings

    ns = sys.argv[2] if len(sys.argv) > 2 else ""
    if not ns:
        sys.exit("用法: python3 dev.py nacos-namespace <namespace-id>")

    st = BootstrapSettings()
    base = f"http://{st.server}/nacos"
    with httpx.Client(timeout=10) as http:
        token = ""
        r = http.post(f"{base}/v1/auth/login",
                      data={"username": st.username, "password": st.password})
        if r.status_code == 200:
            token = r.json().get("accessToken", "")

        params = {"accessToken": token} if token else {}
        existing = http.get(f"{base}/v1/console/namespaces", params=params).json()
        ids = [x.get("namespace") for x in (existing.get("data") or [])]
        if ns in ids:
            print(f"命名空间 {ns} 已存在,跳过")
            return

        r = http.post(f"{base}/v1/console/namespaces", params=params, data={
            "customNamespaceId": ns, "namespaceName": ns,
            "namespaceDesc": f"{ns} environment",
        })
        if r.status_code == 200 and r.text.strip() == "true":
            print(f"已创建命名空间 {ns}")
            print(f"  下一步: NACOS_NAMESPACE={ns} python3 dev.py import-configs")
        else:
            sys.exit(f"创建失败: HTTP {r.status_code} {r.text[:200]}")


@task("import-configs", "把 nacos-configs/*.yaml 导入 Nacos 配置中心")
def import_configs() -> None:
    run(PY, "scripts/import_configs.py", env=_NO_PROXY)


# ---------- 进程 ----------


@task("service", "起 cmdb-service(gRPC,注册到 Nacos)")
def service() -> None:
    run(PY, "-m", "services.cmdb.server", env=_NO_PROXY)


@task("gateway", "起网关(端口由 Nacos http.port 决定,0=随机)")
def gateway() -> None:
    """端口由 Nacos 的 http.port 决定(0 = 随机),所以不走 uvicorn CLI——
    CLI 没法在绑定后把真实端口告诉应用,也读不到 Nacos 里的 forwarded_allow_ips。
    热重载用 GATEWAY_RELOAD=1 打开。"""
    run(PY, "-m", "gateway.main", env={"GATEWAY_RELOAD": "1", **_NO_PROXY})


# ---------- 共享库上的初始化(DBA 手动跑,用 DDL 权限账号) ----------


@task("db-init", "在共享 mysql 上建 cmdb 库 + 只有 DML 权限的 cmdb_app 账号")
def db_init() -> None:
    """共享实例的 docker-entrypoint-initdb.d 只在数据卷首次初始化时跑一次,
    所以每接一个新项目都要手动执行这一步。幂等,可重复跑。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = make_url(load_dotenv()["CMDB_MIGRATOR_DATABASE_URL"])
    # 注意:URL.set(database=None) 是"不改动"的意思,要去掉库名必须传空串,
    # 否则会连去一个还不存在的库,报 Unknown database
    server = create_engine(url.set(database=""), isolation_level="AUTOCOMMIT")
    statements = [
        "CREATE DATABASE IF NOT EXISTS `cmdb` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        "CREATE USER IF NOT EXISTS 'cmdb_app'@'%' IDENTIFIED BY 'cmdb_app'",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON `cmdb`.* TO 'cmdb_app'@'%'",
        "FLUSH PRIVILEGES",
    ]
    with server.connect() as conn:
        for sql in statements:
            print(f"  {sql}")
            conn.execute(text(sql))
    print("完成:接着跑 python3 dev.py db-upgrade 建表")


# ---------- 迁移(DBA 手动跑,用 DDL 权限账号) ----------


@task("db-upgrade", "alembic upgrade head(用 CMDB_MIGRATOR_DATABASE_URL)")
def db_upgrade() -> None:
    run(PY, "-m", "alembic", "-c", "services/cmdb/alembic.ini",
        "upgrade", "head", env=load_dotenv())


@task("db-downgrade", "alembic downgrade -1")
def db_downgrade() -> None:
    run(PY, "-m", "alembic", "-c", "services/cmdb/alembic.ini",
        "downgrade", "-1", env=load_dotenv())


@task("db-revision", "alembic revision --autogenerate -m <说明>(连真库对比 models 生成迁移)")
def db_revision() -> None:
    msg = " ".join(sys.argv[2:]) or "auto"
    run(PY, "-m", "alembic", "-c", "services/cmdb/alembic.ini",
        "revision", "--autogenerate", "-m", msg, env=load_dotenv())


# ---------- Docker ----------


@task("build", "docker compose build")
def build() -> None:
    need("docker")
    run("docker", "compose", "build")


@task("up", "构建并起本项目的容器")
def up() -> None:
    need("docker")
    # 网络由共享中间件栈创建;它没起来的话这里会直接报 network not found
    print("提示:依赖外部网络 devops-net,需先在 infra 目录 docker compose up -d\n")
    run("docker", "compose", "up", "-d", "--build")


@task("down", "停掉整套")
def down() -> None:
    need("docker")
    run("docker", "compose", "down")


@task("logs", "跟随查看日志")
def logs() -> None:
    need("docker")
    run("docker", "compose", "logs", "-f", "--tail=100")


# ---------- 杂项 ----------


# 清理时不进这些目录,免得把虚拟环境和 .git 里的缓存也扫了
SKIP_DIRS = {".venv", "venv", ".git", "node_modules"}


@task("clean", "删掉生成的 stub 和 __pycache__")
def clean() -> None:
    removed = 0
    target = GEN / "cmdb"
    if target.exists():
        shutil.rmtree(target)
        removed += 1
    for cache in ROOT.rglob("__pycache__"):
        if SKIP_DIRS & set(cache.relative_to(ROOT).parts):
            continue
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    print(f"清理了 {removed} 处")


def usage() -> None:
    print(__doc__.strip())
    print("\n可用任务:")
    for name, (help_text, _) in TASKS.items():
        print(f"  {name:<16} {help_text}")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        usage()
        return 0
    name = argv[0]
    if name not in TASKS:
        print(f"未知任务: {name}\n")
        usage()
        return 2
    TASKS[name][1]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
