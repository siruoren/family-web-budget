"""安全加密服务 - 纯标准库实现 (无第三方依赖)

威胁模型: 防止数据库文件 (instance/budget.db) 被直接拷走后泄露金额/备注。
方案: 对每条 Asset 记录的 value/note 做应用层"混淆加密"存储 (密文带 enc:v1: 前缀)。

加密构造 (encrypt-then-MAC, 全部基于 hashlib 标准库):
  - KDF: PBKDF2-HMAC-SHA256 (password + server_pepper, 200000 轮) -> 32B 用户密钥
  - 流密码: HMAC-SHA256(user_key, nonce || counter) 生成的密钥流与明文异或 (计数器模式)
  - 完整性: HMAC-SHA256(derived_mac_key, nonce || ciphertext) 标签 (encrypt-then-MAC)
  - 格式: "enc:v1:" + urlsafe_b64(nonce) + ":" + urlsafe_b64(ct) + ":" + urlsafe_b64(tag)

密钥来源:
  - 每个用户的密钥由 "用户密码 + 全局 pepper" 派生 (pepper 来自 config.yml)
  - 用户解锁后, 密钥缓存在请求级 g.current_user_key, 并以 master_key 包裹写入持久 cookie
  - master_key 由 SECRET_KEY 派生, 仅用于包裹用户密钥存入 cookie, 不参与 DB 加密
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import os
import secrets

from flask import g, session, request, current_app

# 加密相关常量
PBKDF2_ITERS = 200_000          # 用户密钥派生轮数
COOKIE_USER_KEY_DAYS = 30       # 用户解锁 cookie 有效期 (天)
ENC_PREFIX = "enc:v1:"          # 密文前缀 (用于区分遗留明文)

# 流密码分块大小 (HMAC 输出 32 字节)
_BLOCK = 32


# -------------------------------------------------------------- KDF
def _pbkdf2(password: bytes, salt: bytes, iters: int, dklen: int = 32) -> bytes:
    """PBKDF2-HMAC-SHA256"""
    return hashlib.pbkdf2_hmac("sha256", password, salt, iters, dklen=dklen)


def _server_pepper() -> bytes:
    """全局 pepper (来自 config.yml security.encryption_pepper, 否则回退 SECRET_KEY)"""
    cfg = current_app.config.get("SECURITY_CONFIG", {}) or {}
    pep = cfg.get("encryption_pepper") or ""
    if not pep:
        # 回退: 使用 SECRET_KEY (开发环境)
        pep = current_app.config.get("SECRET_KEY", "dev-pepper")
    return pep.encode("utf-8")


def derive_user_key(user_password: str) -> bytes:
    """从用户密码 + 全局 pepper 派生 32 字节用户密钥"""
    return _pbkdf2(
        user_password.encode("utf-8"),
        _server_pepper(),
        PBKDF2_ITERS,
        32,
    )


def _master_key() -> bytes:
    """master_key: 仅用于包裹用户密钥存入 cookie (派生自 SECRET_KEY + pepper)"""
    sk = current_app.config.get("SECRET_KEY", "dev-secret-key")
    return _pbkdf2(
        str(sk).encode("utf-8"),
        _server_pepper() + b"__master__",
        100_000,
        32,
    )


# -------------------------------------------------------------- 对称加密原语
def _xor_stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """HMAC-SHA256 计数器模式流密码: keystream_i = HMAC(key, nonce || i)"""
    out = bytearray()
    counter = 0
    for i in range(0, len(data), _BLOCK):
        block = data[i:i + _BLOCK]
        ks = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(b ^ k for b, k in zip(block, ks))
        counter += 1
    return bytes(out)


def encrypt_bytes(plaintext: bytes, user_key: bytes) -> str:
    """加密字节 -> 密文 token (enc:v1:nonce:ct:tag)"""
    # 从用户密钥再派生 enc_key 与 mac_key (域分离)
    enc_key = hmac.new(user_key, b"enc", hashlib.sha256).digest()
    mac_key = hmac.new(user_key, b"mac", hashlib.sha256).digest()
    nonce = secrets.token_bytes(16)
    ct = _xor_stream(enc_key, nonce, plaintext)
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    parts = [
        base64.urlsafe_b64encode(nonce).decode("ascii"),
        base64.urlsafe_b64encode(ct).decode("ascii"),
        base64.urlsafe_b64encode(tag).decode("ascii"),
    ]
    return ENC_PREFIX + ":".join(parts)


def decrypt_bytes(token: str, user_key: bytes) -> bytes:
    """解密 token -> 明文字节; 校验失败抛 ValueError"""
    if not isinstance(token, str) or not token.startswith(ENC_PREFIX):
        raise ValueError("非密文 token")
    body = token[len(ENC_PREFIX):]
    parts = body.split(":")
    if len(parts) != 3:
        raise ValueError("密文格式错误")
    nonce = base64.urlsafe_b64decode(parts[0])
    ct = base64.urlsafe_b64decode(parts[1])
    tag = base64.urlsafe_b64decode(parts[2])
    enc_key = hmac.new(user_key, b"enc", hashlib.sha256).digest()
    mac_key = hmac.new(user_key, b"mac", hashlib.sha256).digest()
    expect = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, tag):
        raise ValueError("密文完整性校验失败 (密钥不符或数据被篡改)")
    return _xor_stream(enc_key, nonce, ct)


# -------------------------------------------------------------- 字符串/数值包装
def encrypt_str(text: str, user_key: bytes) -> str:
    if text is None:
        text = ""
    return encrypt_bytes(text.encode("utf-8"), user_key)


def decrypt_str(token, user_key: bytes) -> str:
    """解密字符串; 非密文(遗留明文)则原样返回, 保证向后兼容"""
    if token is None:
        return ""
    s = token if isinstance(token, str) else str(token)
    if not s:
        return ""
    if not s.startswith(ENC_PREFIX):
        return s  # 遗留明文, 直接返回
    try:
        return decrypt_bytes(s, user_key).decode("utf-8")
    except Exception:
        return ""  # 解密失败 (密钥缺失/数据损坏) -> 不暴露敏感内容


def encrypt_float(value, user_key: bytes) -> str:
    """浮点 -> 密文 token (定长 repr)"""
    if value is None:
        value = 0.0
    s = repr(float(value))
    return encrypt_bytes(s.encode("utf-8"), user_key)


def decrypt_float(token, user_key: bytes) -> float:
    """解密金额; 非密文(遗留明文数字)则 float() 兼容; 解密失败返回 0"""
    if token is None:
        return 0.0
    s = token if isinstance(token, str) else str(token)
    if not s:
        return 0.0
    if not s.startswith(ENC_PREFIX):
        # 遗留明文数字 (迁移前数据)
        try:
            return float(s)
        except Exception:
            return 0.0
    try:
        return float(decrypt_bytes(s, user_key).decode("utf-8"))
    except Exception:
        return 0.0


def is_encrypted(token) -> bool:
    """判断存储值是否已是密文"""
    if not token:
        return False
    s = token if isinstance(token, str) else str(token)
    return s.startswith(ENC_PREFIX)


# -------------------------------------------------------------- 请求级密钥
def set_current_user_key(key: bytes):
    """缓存当前用户密钥到请求级 g (整个请求复用)"""
    g.current_user_key = key


def get_current_user_key() -> bytes | None:
    """获取当前请求可用的用户密钥

    优先级: g.current_user_key > session 持久 cookie 解包
    返回 None 表示尚未解锁 (需输入用户密码)
    """
    key = getattr(g, "current_user_key", None)
    if key is not None:
        return key
    # 尝试从持久 cookie 恢复
    key = _load_key_from_cookie()
    if key is not None:
        g.current_user_key = key
    return key


# -------------------------------------------------------------- 持久 cookie 包裹
def _cookie_name(uid: int) -> str:
    return f"_uk_{uid}"


def save_key_to_cookie(uid: int, user_key: bytes, response=None):
    """将用户密钥用 master_key 包裹写入持久 cookie (30 天)

    若 response 为 None, 则通过 session 临时记录 (下次响应再写)
    """
    wrapped = _xor_stream(_master_key(), b"__cookie_wrap__", user_key)
    token = base64.urlsafe_b64encode(wrapped).decode("ascii")
    if response is not None:
        response.set_cookie(
            _cookie_name(uid), token,
            max_age=COOKIE_USER_KEY_DAYS * 86400,
            httponly=True, samesite="Lax", secure=False,
            path="/",
        )
    else:
        # 暂存, 由 after_request 写出
        session.setdefault("_pending_cookies", {})[_cookie_name(uid)] = (
            token, COOKIE_USER_KEY_DAYS * 86400
        )
    return token


def _load_key_from_cookie() -> bytes | None:
    """从 cookie 恢复用户密钥 (master_key 解包)"""
    user = getattr(g, "current_user", None)
    if user is None or not getattr(user, "id", None):
        return None
    token = request.cookies.get(_cookie_name(user.id))
    if not token:
        return None
    try:
        wrapped = base64.urlsafe_b64decode(token)
        key = _xor_stream(_master_key(), b"__cookie_wrap__", wrapped)
        return key
    except Exception:
        return None


def clear_user_cookie(uid: int, response=None):
    """清除用户解锁 cookie"""
    if response is not None:
        response.delete_cookie(_cookie_name(uid), path="/")
    else:
        session.setdefault("_pending_cookies", {})[_cookie_name(uid)] = (None, 0)
