"""轻量 CSRF 保护 - 无需 Flask-WTF 依赖

用法:
  1. create_app 中调用 init_csrf(app)
  2. 模板中: <meta name="csrf-token" content="{{ csrf_token() }}">
  3. 表单中: <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  4. AJAX 中: fetch(url, { headers: {"X-CSRFToken": getCSRFToken()} })

  豁免: GET / HEAD / OPTIONS 请求
"""
import secrets
from flask import session, request, g, abort


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# 这些路径前缀豁免 CSRF (AJAX 内部调用)
CSRF_EXEMPT_PREFIXES = ()


def _get_token() -> str:
    """从 session 获取或生成 CSRF token"""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["_csrf_token"] = token
    return token


def _check_csrf() -> None:
    """before_request 钩子: 校验 POST/PUT/DELETE/PATCH 的 CSRF token

    对所有请求预先确保 session 中存在 token (GET 请求也生成),
    保证表单渲染时 context_processor 取到的是同一个 token.
    """
    # 先确保 session 中有 token (对所有 method 生效)
    token = _get_token()

    if request.method in SAFE_METHODS:
        return

    path = request.path
    for prefix in CSRF_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return

    # 表单字段 / JSON body / 自定义 header 任一匹配即可
    submitted = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
    )

    if not submitted or submitted != token:
        abort(403, description="CSRF token 验证失败")


def init_csrf(app):
    """初始化 CSRF 保护"""
    app.before_request(_check_csrf)

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": _get_token}
