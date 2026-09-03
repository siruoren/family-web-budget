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
from ..services import auth

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _app_version() -> str:
    return getattr(current_app, "version", "2.0.0")


# -------------------------------------------------------------- AJAX 分片渲染
# 每个 section 名 -> 分片模板路径; AJAX 操作后只返回受影响的分片 HTML
_SECTION_PARTIAL = {
    "menus": "settings/_section_menus.html",
    "items": "settings/_section_items.html",
    "users": "settings/_section_users.html",
    "import-export": "settings/_section_import_export.html",
    "formula": "settings/_section_formula.html",
    "security": "settings/_section_security.html",
    "locks": "settings/_section_locks.html",
    "sysinfo": "settings/_section_sysinfo.html",
}


def _is_ajax() -> bool:
    """是否为 AJAX 请求 (fetch 带 X-Requested-With 头)"""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" \
        or request.args.get("json") == "1"


def _ctx() -> dict:
    """构建设置页完整上下文 (首页渲染与 AJAX 分片复用)"""
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
        "db_size_kb": round(os.path.getsize(db_path) / 1024, 1)
        if db_path and os.path.exists(db_path) else 0,
        "app_version": _app_version(),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    users = db.session.execute(
        select(User).order_by(User.sort_order, User.id)
    ).scalars().all()
    items = db.session.execute(
        select(AccountItem).order_by(
            AccountItem.type, AccountItem.sort_order, AccountItem.id
        )
    ).scalars().all()
    lock_config = {
        "enabled": is_lock_enabled(),
        "ttl": get_lock_ttl(),
        "heartbeat_interval": get_setting("heartbeat_interval", 60),
        "sync_interval": get_setting("sync_interval", 30),
    }
    return {
        "stats": stats, "sys_info": sys_info,
        "users": users, "items": items,
        "menu_tree": _build_menu_tree(),
        "lock_config": lock_config,
        "active_locks": list_all_locks(),
        "all_settings": db.session.execute(
            select(Setting).order_by(Setting.key)
        ).scalars().all(),
        "formula": get_formula(), "all_types": get_all_types(),
        "security": {"admin_password_configured": auth.admin_password_configured()},
    }


def _render_sections(ctx: dict, *names: str) -> dict:
    """渲染指定分片为 {name: html}, 供前端 outerHTML 替换"""
    return {
        n: render_template(_SECTION_PARTIAL[n], **ctx)
        for n in names if n in _SECTION_PARTIAL
    }


def _done(ok: bool, msg: str, sections=()):
    """统一收尾: AJAX 返回 JSON {ok,msg,sections}; 否则 flash + redirect"""
    ctx = _ctx()
    if _is_ajax():
        return jsonify({
            "ok": ok, "msg": msg,
            "sections": _render_sections(ctx, *sections) if ok else {},
        })
    flash(msg, "success" if ok else "error")
    return redirect(url_for("settings.index", uid=g.current_user.id))


@bp.route("/")
def index():
    """系统配置首页"""
    return render_template("settings/index.html", **_ctx())


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
    password = request.form.get("password", "")
    password2 = request.form.get("password2", "")
    if not name:
        return _done(False, "用户名不能为空")
    if len(password) < 4:
        return _done(False, "密码至少 4 位")
    if password != password2:
        return _done(False, "两次输入的密码不一致")
    existing = db.session.execute(
        select(User).where(User.name == name)
    ).scalars().first()
    if existing:
        return _done(False, f"用户 '{name}' 已存在")
    max_order = db.session.execute(
        select(func.max(User.sort_order))
    ).scalar() or 0
    is_default = request.form.get("is_default") == "on"
    user = User(
        name=name, sort_order=max_order + 1, is_default=is_default,
        password_hash=auth.hash_password(password),
    )
    db.session.add(user)
    if is_default:
        db.session.query(User).filter(User.id != user.id).update(
            {"is_default": False}
        )
    db.session.commit()
    return _done(
        True, f"已创建用户: {name} (已配置访问密码)",
        ("users", "sysinfo", "security"),
    )


@bp.route("/users/<int:user_id>/password", methods=["POST"])
def user_set_password(user_id):
    """设置/修改用户访问密码 (创建用户时已配置, 此处用于改密或为旧用户补设)"""
    user = db.session.get(User, user_id)
    if not user:
        return _done(False, "用户不存在")
    password = request.form.get("password", "")
    password2 = request.form.get("password2", "")
    if len(password) < 4:
        return _done(False, "密码至少 4 位")
    if password != password2:
        return _done(False, "两次输入的密码不一致")
    user.password_hash = auth.hash_password(password)
    db.session.commit()
    # 若修改的是当前用户, 清除其解锁 cookie (下次访问需重新输入新密码)
    if g.current_user.id == user_id:
        auth.lock_user(user_id)
    return _done(
        True, f"已为用户 {user.name} 设置/更新访问密码",
        ("users", "security"),
    )


@bp.route("/users/<int:user_id>/default", methods=["POST"])
def user_set_default(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return _done(False, "用户不存在")
    db.session.query(User).update({"is_default": False})
    user.is_default = True
    db.session.commit()
    return _done(True, f"已设置默认用户: {user.name}", ("users",))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def user_delete(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return _done(False, "用户不存在")
    if user.is_default:
        return _done(False, "不能删除默认用户, 请先设置其他用户为默认")
    name = user.name
    db.session.delete(user)
    db.session.commit()
    return _done(
        True, f"已删除用户: {name} (及其所有数据)",
        ("users", "sysinfo", "security"),
    )


# -------------------------------------------------------------- 账户条目管理
@bp.route("/items/add", methods=["POST"])
def item_add():
    """单个新增条目"""
    name = request.form.get("name", "").strip()
    item_type = request.form.get("type", "").strip()
    owner = request.form.get("owner", "家庭").strip()
    note = request.form.get("note", "").strip()
    if not name or not item_type:
        return _done(False, "名称和类型不能为空")
    existing = db.session.execute(
        select(AccountItem).where(
            AccountItem.name == name,
            AccountItem.type == item_type,
            AccountItem.owner == owner,
        )
    ).scalars().first()
    if existing:
        return _done(False, f"条目已存在: {item_type}/{owner}/{name}")
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0
    db.session.add(AccountItem(
        name=name, type=item_type, owner=owner, note=note,
        sort_order=max_order + 1,
    ))
    db.session.commit()
    return _done(
        True, f"已新增条目: {item_type}/{owner}/{name}",
        ("items", "formula", "sysinfo"),
    )


@bp.route("/items/batch-add", methods=["POST"])
def item_batch_add():
    """批量新增条目 - 每行一条, 格式: 名称,类型,属主,备注"""
    raw = request.form.get("batch_text", "").strip()
    if not raw:
        return _done(False, "批量内容不能为空")
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
    return _done(
        True, f"批量新增: {added} 条新增, {skipped} 条跳过(已存在)",
        ("items", "formula", "sysinfo"),
    )


@bp.route("/items/<int:item_id>/edit", methods=["POST"])
def item_edit(item_id):
    """编辑条目 (每个字段都可以调整)"""
    item = db.session.get(AccountItem, item_id)
    if not item:
        return _done(False, "条目不存在")
    name = request.form.get("name", "").strip()
    item_type = request.form.get("type", "").strip()
    owner = request.form.get("owner", "").strip()
    note = request.form.get("note", "").strip()
    is_active = request.form.get("is_active") == "on"
    sort_order = request.form.get("sort_order", item.sort_order, type=int)
    if not name or not item_type or not owner:
        return _done(False, "名称、类型、属主不能为空")
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
            return _done(False, f"已存在相同条目: {item_type}/{owner}/{name}")
    item.name = name
    item.type = item_type
    item.owner = owner
    item.note = note
    item.is_active = is_active
    item.sort_order = sort_order
    db.session.commit()
    return _done(
        True, f"已更新条目: {item_type}/{owner}/{name}",
        ("items", "formula"),
    )


@bp.route("/items/<int:item_id>/delete", methods=["POST"])
def item_delete(item_id):
    """删除条目 (级联删除 Asset)"""
    item = db.session.get(AccountItem, item_id)
    if not item:
        return _done(False, "条目不存在")
    name = f"{item.type}/{item.owner}/{item.name}"
    db.session.delete(item)
    db.session.commit()
    return _done(True, f"已删除条目: {name}", ("items", "formula", "sysinfo"))


# -------------------------------------------------------------- 公式配置
@bp.route("/formula", methods=["POST"])
def update_formula():
    """更新资产计算公式"""
    expr = request.form.get("formula", "").strip()
    if not expr:
        return _done(False, "公式不能为空")
    set_formula(expr)
    return _done(True, f"公式已更新: {expr}", ("formula",))


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
            return _done(False, "锁 TTL 需在 10~3600 秒之间")
    except ValueError:
        return _done(False, "锁 TTL 必须是整数")
    try:
        hb_val = max(5, int(heartbeat))
        sync_val = max(5, int(sync_iv))
    except ValueError:
        hb_val, sync_val = 60, 30
    set_setting("lock_enabled", "1" if enabled else "0", "bool")
    set_setting("lock_ttl", ttl_val, "int")
    set_setting("heartbeat_interval", hb_val, "int")
    set_setting("sync_interval", sync_val, "int")
    return _done(
        True,
        f"并发锁配置已更新: TTL={ttl_val}s, "
        f"{'启用' if enabled else '已禁用'}",
        ("locks",),
    )


# -------------------------------------------------------------- 锁管理
@bp.route("/locks/force-release/<int:lock_id>", methods=["POST"])
def force_release_lock(lock_id):
    ok = force_release(lock_id)
    if ok:
        return _done(True, f"已强制释放锁 #{lock_id}", ("locks", "sysinfo"))
    return _done(False, f"锁 #{lock_id} 不存在或已释放")


@bp.route("/locks/force-release-all", methods=["POST"])
def force_release_all_locks():
    confirm = request.form.get("confirm", "").strip()
    if confirm != "确认清空":
        return _done(False, "请输入 '确认清空' 以确认")
    count = force_release_all()
    return _done(True, f"已清空 {count} 个活跃锁", ("locks", "sysinfo"))


@bp.route("/locks/cleanup", methods=["POST"])
def cleanup_expired_locks():
    count = cleanup_expired()
    return _done(True, f"已清理 {count} 个过期锁", ("locks", "sysinfo"))


# -------------------------------------------------------------- 菜单管理
@bp.route("/menus/add", methods=["POST"])
def menu_add():
    name = request.form.get("name", "").strip()
    if not name:
        return _done(False, "菜单名称不能为空")
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
    return _done(True, f"已添加菜单: {name}", ("menus",))


@bp.route("/menus/<int:menu_id>/edit", methods=["POST"])
def menu_edit(menu_id):
    mi = db.session.get(MenuItem, menu_id)
    if not mi:
        return _done(False, "菜单项不存在")
    name = request.form.get("name", "").strip()
    if not name:
        return _done(False, "菜单名称不能为空")
    parent_id = request.form.get("parent_id", type=int)
    if parent_id == menu_id:
        return _done(False, "不能将菜单设为自身的子菜单")
    mi.name = name
    mi.parent_id = parent_id if parent_id else None
    mi.filter_type = request.form.get("filter_type", "").strip()
    mi.filter_owner = request.form.get("filter_owner", "").strip()
    mi.sort_order = request.form.get("sort_order", 0, type=int)
    mi.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return _done(True, f"已更新菜单: {name}", ("menus",))


@bp.route("/menus/<int:menu_id>/delete", methods=["POST"])
def menu_delete(menu_id):
    mi = db.session.get(MenuItem, menu_id)
    if not mi:
        return _done(False, "菜单项不存在")
    name = mi.name
    db.session.delete(mi)
    db.session.commit()
    return _done(True, f"已删除菜单: {name} (含子菜单)", ("menus",))


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
        return _done(False, "请选择 JSON 文件")
    try:
        raw = file.read().decode("utf-8-sig")
        data = json.loads(raw)
    except Exception as e:
        return _done(False, f"JSON 解析失败: {e}")

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
    return _done(
        True,
        f"导入完成: 条目 +{added_items}, 菜单 +{added_menus}, 用户 +{added_users}",
        ("menus", "items", "users", "formula", "sysinfo"),
    )


# -------------------------------------------------------------- 数据导入 (CSV)
@bp.route("/import/csv", methods=["POST"])
def import_csv():
    """从 CSV 批量导入账户条目"""
    file = request.files.get("csv_file")
    if not file or not file.filename:
        return _done(False, "请选择 CSV 文件")
    try:
        raw = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
    except Exception as e:
        return _done(False, f"CSV 解析失败: {e}")

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
    return _done(
        True, f"CSV 导入: 新增 {added} 条, 跳过 {skipped} 条(已存在)",
        ("items", "formula", "sysinfo"),
    )


# -------------------------------------------------------------- 历史数据导入 (CSV)
@bp.route("/import/history", methods=["POST"])
def import_history():
    """从 CSV 批量导入历史月度数据 (Asset 价值)

    CSV 表头(长表, 每行一条月度记录):
        年份,月份,类型,属主,条目名称,金额,备注

    逻辑:
      1. 按 (名称+类型+属主) 找或创建 AccountItem (条目模板)
      2. 按 (年+月+条目+当前用户) upsert Asset (月度记录)
      - merge  : 已存在的 Asset 跳过 (不覆盖, 安全)
      - upsert : 已存在的 Asset 用新值覆盖
    """
    file = request.files.get("history_file")
    if not file or not file.filename:
        return _done(False, "请选择 CSV 文件")
    try:
        raw = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
    except Exception as e:
        return _done(False, f"CSV 解析失败: {e}")

    uid = g.current_user.id
    mode = request.form.get("import_mode", "merge")  # merge / upsert

    added_items = added_assets = updated_assets = skipped = 0
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0

    for row in reader:
        name = (row.get("条目名称") or row.get("name") or "").strip()
        itype = (row.get("类型") or row.get("type") or "").strip()
        owner = (row.get("属主") or row.get("owner") or "家庭").strip()
        year_s = (row.get("年份") or row.get("year") or "").strip()
        month_s = (row.get("月份") or row.get("month") or "").strip()
        val_raw = (row.get("金额") or row.get("value") or "").strip()
        note = (row.get("备注") or row.get("note") or "").strip()

        if not name or not itype or not owner or not year_s or not month_s:
            skipped += 1
            continue
        try:
            year_i = int(year_s)
            month_i = int(month_s)
            if not (1 <= month_i <= 12):
                skipped += 1
                continue
        except ValueError:
            skipped += 1
            continue
        try:
            val = float(val_raw)
        except ValueError:
            skipped += 1
            continue

        # 1. 找或创建 AccountItem (名称+类型+属主 唯一)
        item = db.session.execute(
            select(AccountItem).where(
                AccountItem.name == name,
                AccountItem.type == itype,
                AccountItem.owner == owner,
            )
        ).scalars().first()
        if not item:
            max_order += 1
            item = AccountItem(
                name=name, type=itype, owner=owner, note="历史导入",
                sort_order=max_order, is_active=True,
            )
            db.session.add(item)
            db.session.flush()
            added_items += 1

        # 2. upsert Asset (年+月+条目+用户 唯一)
        asset = db.session.execute(
            select(Asset).where(
                Asset.year == year_i, Asset.month == month_i,
                Asset.account_item_id == item.id, Asset.user_id == uid,
            )
        ).scalars().first()
        if asset:
            if mode == "upsert":
                asset.value = val
                asset.note = note
                asset.source = "import"
                updated_assets += 1
            else:
                skipped += 1
        else:
            db.session.add(Asset(
                year=year_i, month=month_i, account_item_id=item.id,
                user_id=uid, value=val, note=note, source="import",
            ))
            added_assets += 1

    db.session.commit()
    return _done(
        True,
        f"历史数据导入完成: 新增条目 {added_items} 个, "
        f"新增记录 {added_assets} 条, 更新 {updated_assets} 条"
        f"{f', 跳过 {skipped} 条' if skipped else ''}",
        ("items", "formula", "sysinfo"),
    )
