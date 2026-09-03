"""系统配置视图 - 用户管理 / 账户条目管理 / 菜单管理 / 公式配置 / 锁管理 / 导入导出 / 系统信息

v2 架构重构
"""
import os
import io
import json
import platform
import csv
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g, current_app, send_file,
)
from sqlalchemy import select, func

from .. import db
from ..models import User, AccountItem, Asset, EditLock, Setting, MenuItem
from ..services.locking import (
    get_setting, set_setting, get_lock_ttl, is_lock_enabled,
    list_all_locks, force_release, force_release_all, cleanup_expired,
)
from ..services.formula import get_formula, set_formula, get_all_types

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _app_version() -> str:
    return getattr(current_app, "version", "2.0.0")


@bp.route("/")
def index():
    """系统配置首页"""
    uid = g.current_user.id

    # ---- 系统信息 ----
    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db_path = db_uri.replace("sqlite:///", "") if db_uri.startswith("sqlite") else db_uri
    stats = {
        "users": db.session.query(func.count(User.id)).scalar() or 0,
        "items": db.session.query(func.count(AccountItem.id)).scalar() or 0,
        "assets": db.session.query(func.count(Asset.id)).scalar() or 0,
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

    # ---- 用户列表 ----
    users = db.session.execute(
        select(User).order_by(User.sort_order, User.id)
    ).scalars().all()

    # ---- 账户条目列表 ----
    items = db.session.execute(
        select(AccountItem).order_by(AccountItem.type, AccountItem.sort_order, AccountItem.id)
    ).scalars().all()

    # ---- 锁配置 ----
    lock_config = {
        "enabled": is_lock_enabled(),
        "ttl": get_lock_ttl(),
        "heartbeat_interval": get_setting("heartbeat_interval", 60),
        "sync_interval": get_setting("sync_interval", 30),
    }

    # ---- 活跃锁 ----
    active_locks = list_all_locks()

    # ---- 全部配置项 ----
    all_settings = db.session.execute(
        select(Setting).order_by(Setting.key)
    ).scalars().all()

    # ---- 菜单树 ----
    menu_tree = _build_menu_tree()

    return render_template(
        "settings/index.html",
        stats=stats, sys_info=sys_info, lock_config=lock_config,
        active_locks=active_locks, all_settings=all_settings,
        users=users, items=items,
        menu_tree=menu_tree,
        formula=get_formula(), all_types=get_all_types(),
    )


def _build_menu_tree() -> list:
    """构建菜单树 (供管理界面展示)"""
    roots = db.session.execute(
        select(MenuItem).where(
            MenuItem.parent_id.is_(None)
        ).order_by(MenuItem.sort_order, MenuItem.id)
    ).scalars().all()

    def _node(m):
        children = db.session.execute(
            select(MenuItem).where(
                MenuItem.parent_id == m.id
            ).order_by(MenuItem.sort_order, MenuItem.id)
        ).scalars().all()
        return {
            "id": m.id, "name": m.name, "sort_order": m.sort_order,
            "is_active": m.is_active,
            "filter_type": m.filter_type or "",
            "filter_owner": m.filter_owner or "",
            "parent_id": m.parent_id,
            "children": [_node(c) for c in children],
        }

    return [_node(r) for r in roots]


def _flat_menu_list() -> list:
    """扁平化菜单列表 (供下拉选择父菜单)"""
    all_items = db.session.execute(
        select(MenuItem).order_by(MenuItem.sort_order, MenuItem.id)
    ).scalars().all()
    return [{"id": m.id, "name": m.name, "parent_id": m.parent_id,
             "sort_order": m.sort_order} for m in all_items]


# -------------------------------------------------------------- 用户管理
@bp.route("/users/add", methods=["POST"])
def user_add():
    name = request.form.get("name", "").strip()
    if not name:
        flash("用户名不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    existing = db.session.execute(
        select(User).where(User.name == name)
    ).scalars().first()
    if existing:
        flash(f"用户 '{name}' 已存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    max_order = db.session.execute(
        select(func.max(User.sort_order))
    ).scalar() or 0
    is_default = request.form.get("is_default") == "on"
    user = User(name=name, sort_order=max_order + 1, is_default=is_default)
    db.session.add(user)
    # 如果设为默认, 取消其他默认
    if is_default:
        db.session.query(User).filter(User.id != user.id).update(
            {"is_default": False}
        )
    db.session.commit()
    flash(f"已创建用户: {name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/users/<int:user_id>/default", methods=["POST"])
def user_set_default(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("用户不存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    db.session.query(User).update({"is_default": False})
    user.is_default = True
    db.session.commit()
    flash(f"已设置默认用户: {user.name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def user_delete(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("用户不存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    if user.is_default:
        flash("不能删除默认用户, 请先设置其他用户为默认", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    name = user.name
    db.session.delete(user)
    db.session.commit()
    flash(f"已删除用户: {name} (及其所有数据)", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


# -------------------------------------------------------------- 账户条目管理
@bp.route("/items/add", methods=["POST"])
def item_add():
    """单个新增条目"""
    name = request.form.get("name", "").strip()
    item_type = request.form.get("type", "").strip()
    owner = request.form.get("owner", "家庭").strip()
    note = request.form.get("note", "").strip()
    if not name or not item_type:
        flash("名称和类型不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    existing = db.session.execute(
        select(AccountItem).where(
            AccountItem.name == name,
            AccountItem.type == item_type,
            AccountItem.owner == owner,
        )
    ).scalars().first()
    if existing:
        flash(f"条目已存在: {item_type}/{owner}/{name}", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0
    db.session.add(AccountItem(
        name=name, type=item_type, owner=owner, note=note,
        sort_order=max_order + 1,
    ))
    db.session.commit()
    flash(f"已新增条目: {item_type}/{owner}/{name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/items/batch-add", methods=["POST"])
def item_batch_add():
    """批量新增条目 - 每行一条, 格式: 名称,类型,属主,备注"""
    raw = request.form.get("batch_text", "").strip()
    if not raw:
        flash("批量内容不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    lines = raw.split("\n")
    added = 0
    skipped = 0
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        item_type = parts[1]
        owner = parts[2] if len(parts) > 2 else "家庭"
        note = parts[3] if len(parts) > 3 else ""
        if not name or not item_type:
            continue
        existing = db.session.execute(
            select(AccountItem).where(
                AccountItem.name == name,
                AccountItem.type == item_type,
                AccountItem.owner == owner,
            )
        ).scalars().first()
        if existing:
            skipped += 1
            continue
        max_order += 1
        db.session.add(AccountItem(
            name=name, type=item_type, owner=owner, note=note,
            sort_order=max_order,
        ))
        added += 1
    db.session.commit()
    flash(f"批量新增: {added} 条新增, {skipped} 条跳过(已存在)", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/items/<int:item_id>/edit", methods=["POST"])
def item_edit(item_id):
    """编辑条目 (每个字段都可以调整)"""
    item = db.session.get(AccountItem, item_id)
    if not item:
        flash("条目不存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    name = request.form.get("name", "").strip()
    item_type = request.form.get("type", "").strip()
    owner = request.form.get("owner", "").strip()
    note = request.form.get("note", "").strip()
    is_active = request.form.get("is_active") == "on"
    sort_order = request.form.get("sort_order", item.sort_order, type=int)
    if not name or not item_type or not owner:
        flash("名称、类型、属主不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    # 检查唯一约束
    if name != item.name or item_type != item.type or owner != item.owner:
        existing = db.session.execute(
            select(AccountItem).where(
                AccountItem.name == name,
                AccountItem.type == item_type,
                AccountItem.owner == owner,
                AccountItem.id != item_id,
            )
        ).scalars().first()
        if existing:
            flash(f"已存在相同条目: {item_type}/{owner}/{name}", "error")
            return redirect(url_for("settings.index", uid=g.current_user.id))
    item.name = name
    item.type = item_type
    item.owner = owner
    item.note = note
    item.is_active = is_active
    item.sort_order = sort_order
    db.session.commit()
    flash(f"已更新条目: {item_type}/{owner}/{name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/items/<int:item_id>/delete", methods=["POST"])
def item_delete(item_id):
    """删除条目 (级联删除 Asset)"""
    item = db.session.get(AccountItem, item_id)
    if not item:
        flash("条目不存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    name = f"{item.type}/{item.owner}/{item.name}"
    db.session.delete(item)
    db.session.commit()
    flash(f"已删除条目: {name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


# -------------------------------------------------------------- 公式配置
@bp.route("/formula", methods=["POST"])
def update_formula():
    """更新资产计算公式"""
    expr = request.form.get("formula", "").strip()
    if not expr:
        flash("公式不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    set_formula(expr)
    flash(f"公式已更新: {expr}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


# -------------------------------------------------------------- 锁配置
@bp.route("/lock-config", methods=["POST"])
def update_lock_config():
    ttl = request.form.get("lock_ttl", "180").strip()
    enabled = request.form.get("lock_enabled") == "on"
    heartbeat = request.form.get("heartbeat_interval", "60").strip()
    sync_iv = request.form.get("sync_interval", "30").strip()
    try:
        ttl_val = int(ttl)
        if ttl_val < 10 or ttl_val > 3600:
            flash("锁 TTL 需在 10~3600 秒之间", "error")
            return redirect(url_for("settings.index", uid=g.current_user.id))
    except ValueError:
        flash("锁 TTL 必须是整数", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
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
        f"{'启用' if enabled else '已禁用'}",
        "success",
    )
    return redirect(url_for("settings.index", uid=g.current_user.id))


# -------------------------------------------------------------- 锁管理
@bp.route("/locks/force-release/<int:lock_id>", methods=["POST"])
def force_release_lock(lock_id):
    ok = force_release(lock_id)
    if ok:
        flash(f"已强制释放锁 #{lock_id}", "success")
    else:
        flash(f"锁 #{lock_id} 不存在或已释放", "warning")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/locks/force-release-all", methods=["POST"])
def force_release_all_locks():
    confirm = request.form.get("confirm", "").strip()
    if confirm != "确认清空":
        flash("请输入 '确认清空' 以确认", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id))
    count = force_release_all()
    flash(f"已清空 {count} 个活跃锁", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/locks/cleanup", methods=["POST"])
def cleanup_expired_locks():
    count = cleanup_expired()
    flash(f"已清理 {count} 个过期锁", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id))


# -------------------------------------------------------------- 菜单管理
@bp.route("/menus/add", methods=["POST"])
def menu_add():
    name = request.form.get("name", "").strip()
    if not name:
        flash("菜单名称不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")
    parent_id = request.form.get("parent_id", type=int)
    filter_type = request.form.get("filter_type", "").strip()
    filter_owner = request.form.get("filter_owner", "").strip()
    sort_order = request.form.get("sort_order", 0, type=int)
    mi = MenuItem(
        name=name, parent_id=parent_id if parent_id else None,
        filter_type=filter_type, filter_owner=filter_owner,
        sort_order=sort_order, is_active=True,
    )
    db.session.add(mi)
    db.session.commit()
    flash(f"已添加菜单: {name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")


@bp.route("/menus/<int:menu_id>/edit", methods=["POST"])
def menu_edit(menu_id):
    mi = db.session.get(MenuItem, menu_id)
    if not mi:
        flash("菜单项不存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")
    name = request.form.get("name", "").strip()
    if not name:
        flash("菜单名称不能为空", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")
    parent_id = request.form.get("parent_id", type=int)
    if parent_id == menu_id:
        flash("不能将菜单设为自身的子菜单", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")
    mi.name = name
    mi.parent_id = parent_id if parent_id else None
    mi.filter_type = request.form.get("filter_type", "").strip()
    mi.filter_owner = request.form.get("filter_owner", "").strip()
    mi.sort_order = request.form.get("sort_order", 0, type=int)
    mi.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    flash(f"已更新菜单: {name}", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")


@bp.route("/menus/<int:menu_id>/delete", methods=["POST"])
def menu_delete(menu_id):
    mi = db.session.get(MenuItem, menu_id)
    if not mi:
        flash("菜单项不存在", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")
    name = mi.name
    db.session.delete(mi)
    db.session.commit()
    flash(f"已删除菜单: {name} (含子菜单)", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id) + "#menus")


# -------------------------------------------------------------- 模板下载
@bp.route("/template/download")
def download_template():
    """下载账户条目模板 (CSV)"""
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["名称", "类型", "属主", "备注"])
    writer.writerow(["工资", "收入", "家庭", "月度工资收入"])
    writer.writerow(["餐饮", "支出", "家庭", "日常饮食"])
    writer.writerow(["现金结余", "结余", "家庭", "月末现金"])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="account_items_template.csv",
    )


# -------------------------------------------------------------- JSON 导出
@bp.route("/export/json")
def export_json():
    """导出数据库项目和菜单结构为 JSON"""
    items = db.session.execute(
        select(AccountItem).order_by(AccountItem.type, AccountItem.sort_order)
    ).scalars().all()
    menus_all = db.session.execute(
        select(MenuItem).order_by(MenuItem.sort_order, MenuItem.id)
    ).scalars().all()
    users = db.session.execute(
        select(User).order_by(User.sort_order)
    ).scalars().all()
    settings_all = db.session.execute(
        select(Setting).order_by(Setting.key)
    ).scalars().all()

    data = {
        "export_time": datetime.now().isoformat(),
        "version": "2.0",
        "account_items": [
            {"name": it.name, "type": it.type, "owner": it.owner,
             "note": it.note or "", "sort_order": it.sort_order,
             "is_active": it.is_active}
            for it in items
        ],
        "menu_items": [
            {"name": m.name, "parent_id": m.parent_id,
             "filter_type": m.filter_type or "",
             "filter_owner": m.filter_owner or "",
             "sort_order": m.sort_order, "is_active": m.is_active}
            for m in menus_all
        ],
        "users": [
            {"name": u.name, "is_default": u.is_default,
             "sort_order": u.sort_order}
            for u in users
        ],
        "settings": [
            {"key": s.key, "value": s.value, "vtype": s.vtype}
            for s in settings_all
        ],
    }
    buf = io.BytesIO()
    buf.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf, mimetype="application/json", as_attachment=True,
        download_name=f"family_budget_export_{datetime.now().strftime('%Y%m%d')}.json",
    )


# -------------------------------------------------------------- JSON 导入
@bp.route("/import/json", methods=["POST"])
def import_json():
    """导入 JSON (菜单结构 + 账户条目 + 用户 + 配置)"""
    file = request.files.get("json_file")
    if not file or not file.filename:
        flash("请选择 JSON 文件", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#import-export")
    try:
        raw = file.read().decode("utf-8-sig")
        data = json.loads(raw)
    except Exception as e:
        flash(f"JSON 解析失败: {e}", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#import-export")

    mode = request.form.get("import_mode", "merge")
    added_items = added_menus = added_users = 0

    if mode == "replace":
        db.session.query(Asset).delete()
        db.session.query(AccountItem).delete()
        db.session.query(MenuItem).delete()
        db.session.commit()

    # 用户
    for u in data.get("users", []):
        existing = db.session.execute(
            select(User).where(User.name == u["name"])
        ).scalars().first()
        if existing:
            if mode == "replace":
                existing.is_default = u.get("is_default", False)
                existing.sort_order = u.get("sort_order", 0)
        else:
            db.session.add(User(
                name=u["name"], is_default=u.get("is_default", False),
                sort_order=u.get("sort_order", 0),
            ))
            added_users += 1

    # 账户条目
    for it in data.get("account_items", []):
        existing = db.session.execute(
            select(AccountItem).where(
                AccountItem.name == it["name"],
                AccountItem.type == it["type"],
                AccountItem.owner == it["owner"],
            )
        ).scalars().first()
        if existing:
            if mode == "replace":
                existing.note = it.get("note", "")
                existing.sort_order = it.get("sort_order", 0)
                existing.is_active = it.get("is_active", True)
        else:
            db.session.add(AccountItem(
                name=it["name"], type=it["type"], owner=it["owner"],
                note=it.get("note", ""), sort_order=it.get("sort_order", 0),
                is_active=it.get("is_active", True),
            ))
            added_items += 1

    # 菜单项 (需要处理 parent_id 映射)
    old_to_new: dict[int, int] = {}
    for m in data.get("menu_items", []):
        old_parent = m.get("parent_id")
        new_parent = old_to_new.get(old_parent) if old_parent else None
        mi = MenuItem(
            name=m["name"], parent_id=new_parent,
            filter_type=m.get("filter_type", ""),
            filter_owner=m.get("filter_owner", ""),
            sort_order=m.get("sort_order", 0),
            is_active=m.get("is_active", True),
        )
        db.session.add(mi)
        db.session.flush()
        old_to_new[m.get("id", mi.id)] = mi.id
        added_menus += 1

    # 配置项
    for s in data.get("settings", []):
        existing = db.session.execute(
            select(Setting).where(Setting.key == s["key"])
        ).scalars().first()
        if existing:
            existing.value = s["value"]
            existing.vtype = s.get("vtype", "str")
        else:
            db.session.add(Setting(
                key=s["key"], value=s["value"],
                vtype=s.get("vtype", "str"),
            ))

    db.session.commit()
    flash(
        f"导入完成: 条目 +{added_items}, 菜单 +{added_menus}, 用户 +{added_users}",
        "success",
    )
    return redirect(url_for("settings.index", uid=g.current_user.id) + "#import-export")


# -------------------------------------------------------------- 数据导入 (CSV)
@bp.route("/import/csv", methods=["POST"])
def import_csv():
    """从 CSV 批量导入账户条目"""
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("请选择 CSV 文件", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#import-export")
    try:
        raw = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
    except Exception as e:
        flash(f"CSV 解析失败: {e}", "error")
        return redirect(url_for("settings.index", uid=g.current_user.id) + "#import-export")

    added, skipped = 0, 0
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0

    for row in reader:
        name = (row.get("名称") or row.get("name") or "").strip()
        itype = (row.get("类型") or row.get("type") or "").strip()
        owner = (row.get("属主") or row.get("owner") or "家庭").strip()
        note = (row.get("备注") or row.get("note") or "").strip()
        if not name or not itype:
            continue
        existing = db.session.execute(
            select(AccountItem).where(
                AccountItem.name == name,
                AccountItem.type == itype,
                AccountItem.owner == owner,
            )
        ).scalars().first()
        if existing:
            skipped += 1
            continue
        max_order += 1
        db.session.add(AccountItem(
            name=name, type=itype, owner=owner, note=note,
            sort_order=max_order, is_active=True,
        ))
        added += 1
    db.session.commit()
    flash(f"CSV 导入: 新增 {added} 条, 跳过 {skipped} 条(已存在)", "success")
    return redirect(url_for("settings.index", uid=g.current_user.id) + "#import-export")
