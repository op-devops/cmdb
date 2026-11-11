"""精简 SSH 客户端:只做主机验证用得到的几件事——连一下、推公钥、生成密钥对。

迁自老运维平台 libs/ssh.py,砍掉了交互式终端、命令执行、文件传输那些验证用不到的部分。
"""

import io

import paramiko


class SSHError(Exception):
    """连接或认证失败。message 是给用户看的中文原因,直接透传到前端。"""


def _load_pkey(pem: str):
    """从 PEM 文本加载私钥,自动识别 RSA / Ed25519 / ECDSA。"""
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem))
        except paramiko.SSHException:
            continue
    raise SSHError("无法识别的私钥格式")


def connect(hostname, port, username, *, pkey=None, password=None, timeout=10):
    """建立 SSH 连接,返回 client;认证失败/超时抛 SSHError(中文原因)。调用方负责 close()。

    look_for_keys / allow_agent 都关掉:只用显式传进来的凭据,
    不去翻本机 ~/.ssh 和 ssh-agent——服务跑在容器里,那些是不确定因素。
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=hostname, port=port, username=username,
            pkey=_load_pkey(pkey) if pkey else None,
            password=password or None,
            timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
            look_for_keys=False, allow_agent=False,
        )
    except paramiko.AuthenticationException:
        client.close()
        raise SSHError("认证失败")
    except Exception as e:
        client.close()
        raise SSHError(f"连接失败: {e}")
    return client


def add_public_key(client, public_key: str) -> None:
    """把公钥追加到主机的 ~/.ssh/authorized_keys。已存在则不重复加,可反复验证。"""
    cmd = (
        "mkdir -p -m 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys && "
        f"(grep -qF {public_key!r} ~/.ssh/authorized_keys || "
        f"echo {public_key!r} >> ~/.ssh/authorized_keys)"
    )
    _, stdout, stderr = client.exec_command(cmd, timeout=10)
    if stdout.channel.recv_exit_status() != 0:
        raise SSHError(f"写入公钥失败: {stderr.read().decode('utf-8', 'replace')}")


def generate_key() -> tuple[str, str]:
    """生成一对 RSA 密钥,返回 (私钥 PEM, 公钥 openssh 单行)。给全局密钥用,跑一次即可。"""
    key = paramiko.RSAKey.generate(2048)
    buf = io.StringIO()
    key.write_private_key(buf)
    return buf.getvalue(), f"ssh-rsa {key.get_base64()}"
