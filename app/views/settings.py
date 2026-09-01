"""系统后台配置视图 - 独立配置页面

功能模块:
  1. 用户标识设置 (昵称)
  2. 并发锁配置 (TTL / 开关 / 心跳间隔)
  3. 活跃锁管理 (查看 / 强制释放)
  4. 系统信息 (数据库 / 表行数 / 运行环境)
"""
import os
import platform
import sys
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g, current_app,
)
from sqlalchemy import select, func

from .. import db
from ..models import Item, Entry, Account, BalanceSnapshot, ImportLog, EditLock
from ..services.locking import (
    get_setting, set_setting, get_lock_ttl, is_lock_enabled,
    list_all_locks, force_release, force_release_all, cleanup_expired,
)

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _app_version() -> str:
    return getattr(current_app, "version", "1.0.0")


@bp.route("/")
def index():
    """系统配置首页"""
    # ---- 系统信息 ----
    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db_path = db_uri.replace("sqlite:///", "") if db_uri.startswith("sqlite") else db_uri
    stats = {
        "items": db.session.query(func.count(Item.id)).scalar() or 0,
        "entries": db.session.query(func.count(Entry.id)).scalar() or 0,
        "accounts": db.session.query(func.count(Account.id)).scalar() or 0,
        "snapshots": db.session.query(func.count(BalanceSnapshot.id)).scalar() or 0,
        "import_logs": db.session.query(func.count(ImportLog.id)).scalar() or 0,
        "active_locks": db.session.query(func.count(EditLock.id)).scalar() or 0,
    }
    sys_info = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "flask_debug": current_app.config.get("DEBUG", False),
        "db_path": db_path,
        "db_exists": os.path.exists(db_path) if db_path else False,
        "db_size_kb": round(os.path.getsize(db_path) / 1024, 1) if db_path and os.path.exists(db_path) else 0,
        "app_version": _app_version(),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # ---- 锁配置 ----
    lock_config = {
        "enabled": is_lock_enabled(),
        "ttl": get_lock_ttl(),
        "heartbeat_interval": get_setting("heartbeat_interval", 60),
        "sync_interval": get_setting("sync_interval", 30),
    }

    # ---- 活跃锁列表 ----
    active_locks = list_all_locks()

    # ---- 全部配置项 (键值表) ----
    from ..models import Setting
    all_settings = db.session.execute(
        select(Setting).order_by(Setting.key)
    ).scalars().all()

    return render_template(
        "settings/index.html",
        stats=stats, sys_info=sys_info, lock_config=lock_config,
        active_locks=active_locks, all_settings=all_settings,
        my_user_id=g.user_id, my_user_label=session.get("user_label", ""),
    )


# -------------------------------------------------------------- 用户昵称
@bp.route("/profile", methods=["POST"])
def update_profile():
    """设置当前用户昵称 (存 session)"""
    label = request.form.get("user_label", "").strip()
    if not label:
        flash("昵称不能为空", "error")
        return redirect(url_for("settings.index"))
    if len(label) > 32:
        flash("昵称过长 (最多 32 字符)", "error")
        return redirect(url_for("settings.index"))
    session["user_label"] = label
    g.user_label = label
    flash(f"已设置昵称为: {label}", "success")
    return redirect(url_for("settings.index"))


# -------------------------------------------------------------- 锁配置
@bp.route("/lock-config", methods=["POST"])
def update_lock_config():
    """更新并发锁配置 (持久化到 setting 表)"""
    ttl = request.form.get("lock_ttl", "180").strip()
    enabled = request.form.get("lock_enabled") == "on"
    heartbeat = request.form.get("heartbeat_interval", "60").strip()
    sync_iv = request.form.get("sync_interval", "30").strip()

    try:
        ttl_val = int(ttl)
        if ttl_val < 10 or ttl_val > 3600:
            flash("锁 TTL 需在 10~3600 秒之间", "error")
            return redirect(url_for("settings.index"))
    except ValueError:
        flash("锁 TTL 必须是整数", "error")
        return redirect(url_for("settings.index"))

    try:
        hb_val = max(5, int(heartbeat))
        sync_val = max(5, int(sync_iv))
    except ValueError:
        hb_val, sync_val = 60, 30

    set_setting("lock_enabled", "1" if enabled else "0", "bool")
    set_setting("lock_ttl", ttl_val, "int")
    set_setting("heartbeat_interval", hb_val, "int")
    set_setting("sync_interval", sync_val, "int")

    flash(
        f"并发锁配置已更新: TTL={ttl_val}s, "
        f"{'启用' if enabled else '已禁用'}, 心跳={hb_val}s, 同步={sync_val}s",
        "success",
    )
    return redirect(url_for("settings.index"))


# -------------------------------------------------------------- 锁管理
@bp.route("/locks/force-release/<int:lock_id>", methods=["POST"])
def force_release_lock(lock_id):
    """强制释放单个锁"""
    ok = force_release(lock_id)
    if ok:
        flash(f"已强制释放锁 #{lock_id}", "success")
    else:
        flash(f"锁 #{lock_id} 不存在或已释放", "warning")
    return redirect(url_for("settings.index"))


@bp.route("/locks/force-release-all", methods=["POST"])
def force_release_all_locks():
    """一键清空所有锁"""
    confirm = request.form.get("confirm", "").strip()
    if confirm != "确认清空":
        flash("请输入 '确认清空' 以确认清空所有锁", "error")
        return redirect(url_for("settings.index"))
    count = force_release_all()
    flash(f"已清空 {count} 个活跃锁", "success")
    return redirect(url_for("settings.index"))


@bp.route("/locks/cleanup", methods=["POST"])
def cleanup_expired_locks():
    """手动清理过期锁"""
    count = cleanup_expired()
    flash(f"已清理 {count} 个过期锁", "success")
    return redirect(url_for("settings.index"))
