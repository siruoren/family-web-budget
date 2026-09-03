"""认证与访问门禁服务

职责:
  1. 用户密码哈希/校验 (PBKDF2-HMAC-SHA256, 纯标准库)
  2. 管理员密码 (来自 config.yml security.admin_password, 启动时加载并哈希缓存)
  3. 用户解锁态: 首次输入密码后, 派生密钥以 master_key 包裹写入持久 cookie (30 天)
  4. 管理员解锁态: 会话级 (每次浏览器会话进入系统管理需输一次)
  5. before_request 网关: 用户解锁 + /settings 管理员门禁

解锁语义:
  - 用户无密码 -> 不门禁 (可自由访问), 可在系统管理页为其设置密码
  - 用户有密码 且 未解锁 -> 重定向到用户登录页
  - /settings/* 且 管理员密码已配置 且 未解锁 -> 重定向到管理员登录页
  - 系统未配置任何密码 (首次运行) -> 全部放行, 便于初始化
"""
from __future__ import annotations
import hashlib
import hmac

from flask import (
    g, session, request, redirect, url_for, current_app, abort,
)

from . import crypto

# 会话键
_SESS_ADMIN_UNLOCKED = "_admin_unlocked"
_SESS_ADMIN_UID = "_admin_uid"

# 密码哈希参数
PW_ITERS = 200_000


# -------------------------------------------------------------- 密码哈希
def hash_password(password: str) -> str:
    """生成密码哈希: pbkdf2$iters$salt_b64$hash_b64"""
    salt = hashlib.sha256(
        (password + str(current_app.config.get("SECRET_KEY", ""))).encode("utf-8")
    ).digest()[:16]
    # 再加随机盐, 增强唯一性
    import secrets
    salt = salt + secrets.token_bytes(4)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PW_ITERS, 32)
    return f"pbkdf2${PW_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码"""
    if not stored or not password:
        return False
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, 32)
    return hmac.compare_digest(dk, expected)


# -------------------------------------------------------------- 管理员密码 (config)
def admin_password_configured() -> bool:
    """是否在 config.yml 配置了管理员密码"""
    sec = current_app.config.get("SECURITY_CONFIG", {}) or {}
    return bool(sec.get("admin_password"))


def _admin_password_hash() -> str:
    """取管理员密码哈希 (优先 DB 缓存, 否则从 config 明文即时哈希)"""
    # 缓存到 app 对象, 避免每次请求重算
    cached = getattr(current_app, "_admin_pw_hash", None)
    if cached:
        return cached
    sec = current_app.config.get("SECURITY_CONFIG", {}) or {}
    raw = sec.get("admin_password") or ""
    if not raw:
        return ""
    h = hash_password(raw)
    current_app._admin_pw_hash = h
    return h


def verify_admin_password(password: str) -> bool:
    h = _admin_password_hash()
    if not h:
        return False
    return verify_password(password, h)


# -------------------------------------------------------------- 解锁态查询
def is_admin_unlocked() -> bool:
    return bool(session.get(_SESS_ADMIN_UNLOCKED))


def is_user_unlocked(uid: int) -> bool:
    """用户是否已解锁: 持久 cookie 存在且能解包出密钥"""
    if crypto.get_current_user_key() is not None:
        return True
    return False


def mark_admin_unlocked(value: bool = True):
    if value:
        session[_SESS_ADMIN_UNLOCKED] = True
        session[_SESS_ADMIN_UID] = getattr(getattr(g, "current_user", None), "id", 0)
    else:
        session.pop(_SESS_ADMIN_UNLOCKED, None)
        session.pop(_SESS_ADMIN_UID, None)


def unlock_user(uid: int, password: str) -> tuple[bool, str]:
    """验证用户密码并解锁: 校验通过则派生密钥写入持久 cookie

    返回 (ok, message)
    """
    from ..models import User
    from .. import db
    user = db.session.get(User, uid)
    if not user:
        return False, "用户不存在"
    if not user.password_hash:
        return False, "该用户尚未设置密码"
    if not verify_password(password, user.password_hash):
        return False, "密码错误"
    # 派生密钥, 写持久 cookie (暂存到 session, 由 after_request 写出)
    user_key = crypto.derive_user_key(password)
    crypto.set_current_user_key(user_key)
    crypto.save_key_to_cookie(uid, user_key)
    return True, "解锁成功"


def lock_user(uid: int):
    """锁定用户 (清除 cookie)"""
    crypto.clear_user_cookie(uid)


# -------------------------------------------------------------- before_request 网关
# 豁免前缀: 静态资源 + 认证蓝图自身
_EXEMPT_PREFIXES = ("/static/", "/auth/",)


def _is_auth_exempt() -> bool:
    ep = request.endpoint or ""
    path = request.path
    if path.startswith(_EXEMPT_PREFIXES):
        return True
    # auth 蓝图端点
    if ep.startswith("auth."):
        return True
    return False


def _need_user_unlock() -> bool:
    """当前用户是否需要先解锁 (有密码且未解锁)"""
    user = getattr(g, "current_user", None)
    if user is None:
        return False
    if not getattr(user, "password_hash", None):
        return False  # 无密码, 不门禁
    return crypto.get_current_user_key() is None


def _need_admin_unlock() -> bool:
    """访问 /settings 是否需要管理员解锁"""
    if not request.path.startswith("/settings"):
        return False
    if not admin_password_configured():
        return False
    return not is_admin_unlocked()


def _write_pending_cookies(response):
    """after_request: 把暂存的 cookie 写出"""
    pending = session.pop("_pending_cookies", None)
    if not pending:
        return response
    for name, (token, max_age) in pending.items():
        if token is None:
            response.delete_cookie(name, path="/")
        else:
            response.set_cookie(
                name, token, max_age=max_age,
                httponly=True, samesite="Lax", path="/",
            )
    return response


def _auth_gate():
    """before_request: 用户解锁 + 管理员门禁"""
    if _is_auth_exempt():
        return None
    # 管理员门禁 (优先于用户门禁, 因为 /settings 也属于"用户访问")
    if _need_admin_unlock():
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.admin_login", next=nxt))
    # 用户解锁门禁
    if _need_user_unlock():
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.user_login", next=nxt, uid=g.current_user.id))
    return None


def init_auth(app):
    """注册认证网关 + after_request 写 cookie"""
    app.before_request(_auth_gate)

    @app.after_request
    def _after(response):
        return _write_pending_cookies(response)

    @app.context_processor
    def _inject_auth():
        return {
            "is_admin_unlocked": is_admin_unlocked,
            "admin_password_configured": admin_password_configured,
            "user_has_password": lambda: bool(
                getattr(getattr(g, "current_user", None), "password_hash", None)
            ),
        }
