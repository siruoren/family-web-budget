"""认证视图 - 用户登录 / 管理员登录 / 登出

路由:
  GET/POST /auth/user-login   用户密码验证 (解锁, 写持久 cookie)
  GET/POST /auth/admin-login  管理员密码验证 (会话级解锁)
  GET      /auth/logout       锁定当前用户 + 清除管理员解锁
"""
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, g, current_app,
)

from .. import db
from ..models import User
from ..services import auth, crypto

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next(default: str = ".") -> str:
    """安全的回跳地址: 仅允许本站相对路径, 防 open redirect"""
    nxt = request.args.get("next") or request.form.get("next") or ""
    if not nxt:
        return url_for(default)
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for(default)


@bp.route("/user-login", methods=["GET", "POST"])
def user_login():
    uid = request.args.get("uid", type=int) or g.current_user.id
    user = db.session.get(User, uid)
    if not user:
        flash("用户不存在", "error")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        password = request.form.get("password", "")
        # 如果用户没有设置密码，允许空密码登录
        if not user.password_hash:
            # 无密码用户直接生成密钥并登录
            user_key = crypto.derive_user_key("")  # 使用空密码派生密钥
            crypto.set_current_user_key(user_key)
            crypto.save_key_to_cookie(uid, user_key)
            flash("登录成功", "success")
            return redirect(_safe_next("dashboard.index"))
        
        # 有密码用户需要验证
        ok, msg = auth.unlock_user(uid, password)
        if ok:
            flash(msg, "success")
            return redirect(_safe_next("dashboard.index"))
        flash(msg, "error")
        return redirect(url_for("auth.user_login", uid=uid,
                                next=request.args.get("next", "")))

    return render_template(
        "auth/animated_login.html",
        user=user, next_url=request.args.get("next", ""),
        all_users=db.session.query(User).order_by(User.sort_order, User.id).all(),
    )


@bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if not auth.admin_password_configured():
        flash("尚未配置管理员密码, 请在 config.yml 的 security.admin_password 设置后重启", "warning")
        return redirect(url_for("settings.index"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if auth.verify_admin_password(password):
            auth.mark_admin_unlocked(True)
            flash("管理员验证通过", "success")
            return redirect(_safe_next("settings.index"))
        flash("管理员密码错误", "error")
        return redirect(url_for("auth.admin_login", next=request.args.get("next", "")))

    return render_template(
        "auth/admin_login.html",
        next_url=request.args.get("next", ""),
    )


@bp.route("/logout")
def logout():
    user = getattr(g, "current_user", None)
    if user:
        auth.lock_user(user.id)
    auth.mark_admin_unlocked(False)
    flash("已锁定, 下次访问需重新验证", "info")
    return redirect(url_for("dashboard.index"))
