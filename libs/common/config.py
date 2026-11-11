"""引导配置:进程启动只依赖 NACOS_* 环境变量(或 .env)。

其余一切运行配置(端口、上游、DSN、日志级别……)都在 Nacos 配置中心,
通过 libs.common.nacos_client.RemoteConfig 加载并热更新。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NACOS_", env_file=".env", extra="ignore")

    server: str = "nacos:8848"
    namespace: str = "prod"  # 命名空间 ID;public 的 ID 是空字符串
    group: str = "DEFAULT_GROUP"
    username: str = "nacos"
    password: str = "nacos"
