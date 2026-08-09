"""密码加密模块（AES-256-GCM）单元测试。"""

from __future__ import annotations

import pytest

from mcp_dbtools.crypto import CryptoError, decrypt_password, encrypt_password


def test_roundtrip():
    enc = encrypt_password("Gauss@123", "my-secret-key")
    assert enc.startswith("{ENC:")
    assert "Gauss@123" not in enc  # 密文不含明文
    payload = enc[len("{ENC:") : -1]
    assert decrypt_password(payload, "my-secret-key") == "Gauss@123"


def test_empty_password_ok():
    enc = encrypt_password("", "k")
    payload = enc[len("{ENC:") : -1]
    assert decrypt_password(payload, "k") == ""


def test_different_keys_fail():
    enc = encrypt_password("secret", "keyA")
    payload = enc[len("{ENC:") : -1]
    with pytest.raises(CryptoError):
        decrypt_password(payload, "keyB")


def test_tampered_ciphertext_fails():
    enc = encrypt_password("secret", "k")
    payload = enc[len("{ENC:") : -1]
    # 篡改密文中间一个字符 -> GCM 认证失败
    mid = len(payload) // 2
    altered = payload[:mid] + ("A" if payload[mid] != "A" else "B") + payload[mid + 1 :]
    with pytest.raises(CryptoError):
        decrypt_password(altered, "k")


def test_invalid_base64_fails():
    with pytest.raises(CryptoError):
        decrypt_password("!!!not-base64!!!", "k")


def test_encrypt_requires_secret():
    with pytest.raises(CryptoError):
        encrypt_password("secret", "")


def test_decrypt_requires_secret():
    enc = encrypt_password("secret", "k")
    payload = enc[len("{ENC:") : -1]
    with pytest.raises(CryptoError):
        decrypt_password(payload, "")
