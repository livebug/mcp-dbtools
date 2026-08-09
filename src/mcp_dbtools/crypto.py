"""密码加密存储：AES-256-GCM（认证加密）。

数据源密码不再以明文写入 datasources.json，而是存成加密串：

    "password": "{ENC:<base64(nonce + ciphertext + tag)>}"

运行时用环境变量 MCP_DBTOOLS_SECRET_KEY 派生密钥解密。

- 使用 cryptography 的 AES-GCM（AEAD：密文 + 认证标签，可检测篡改）；
- 密钥由 MCP_DBTOOLS_SECRET_KEY 经 SHA-256 派生为 32 字节（AES-256）；
- 密文格式：12 字节随机 nonce + 密文 + 16 字节 tag，整体 base64 编码。

注意：本机制用于「防止配置文件/备份泄露时密码可读」，不替代传输层安全
（对外访问请走 HTTPS / 内网）。
"""

from __future__ import annotations

import base64
import hashlib
import secrets

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except ImportError:  # 未安装 cryptography 时显式报错而非静默降级
    AESGCM = None  # type: ignore[assignment]
    _HAS_CRYPTO = False

_ENC_PREFIX = "{ENC:"
_ENC_SUFFIX = "}"
_NONCE_LEN = 12
_TAG_LEN = 16


class CryptoError(Exception):
    """密码加密/解密错误。"""


def _require_crypto() -> None:
    if not _HAS_CRYPTO:
        raise CryptoError(
            "密码加密需要 cryptography 库，请安装：pip install cryptography "
            "（离线部署请确保离线包 wheels 中包含 cryptography）"
        )


def _key_from_secret(secret: str) -> bytes:
    """由密钥文本派生 32 字节 AES-256 密钥（SHA-256）。"""
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_password(plain: str, secret: str) -> str:
    """将明文密码加密为 {ENC:...} 串。

    secret: MCP_DBTOOLS_SECRET_KEY（任意长度字符串，运行解密时需相同）。
    """
    _require_crypto()
    if not secret:
        raise CryptoError("加密需要 MCP_DBTOOLS_SECRET_KEY，不能为空")
    key = _key_from_secret(secret)
    nonce = secrets.token_bytes(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plain.encode("utf-8"), None)
    payload = base64.b64encode(nonce + ct).decode("ascii")
    return f"{_ENC_PREFIX}{payload}{_ENC_SUFFIX}"


def decrypt_password(payload: str, secret: str) -> str:
    """解密 {ENC:...} 串中的 base64 负载，返回明文密码。

    payload 应不含 {ENC: 与 } 前缀/后缀（config 层已剥除）。
    密钥错误或密文被篡改时抛 CryptoError。
    """
    _require_crypto()
    if not secret:
        raise CryptoError("解密需要 MCP_DBTOOLS_SECRET_KEY，不能为空")
    key = _key_from_secret(secret)
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as exc:
        raise CryptoError("密码密文格式无效（应为合法 base64）") from exc
    if len(raw) < _NONCE_LEN + _TAG_LEN:
        raise CryptoError("密码密文长度无效")
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("密码解密失败：MCP_DBTOOLS_SECRET_KEY 错误或密文被篡改") from exc
