"""系统配置视图 - 用户管理 / 账户条目管理 / 菜单管理 / 公式配置 / 锁管理 / 导入导出 / 系统信息

v2 架构重构
"""
import os
import io
import platform
import csv
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g, current_app, send_file,
)
from sqlalchemy import select, func

from .. import db
from ..models import (
    User, AccountItem, Asset, Setting, MenuItem, ItemType,
)
from ..services.settings import get_setting, set_setting
from ..services.formula import get_formula, set_formula, get_all_types
from ..services import auth

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _app_version() -> str:
    return getattr(current_app, "version", "2.0.0")


# -------------------------------------------------------------- AJAX 分片渲染
# 每个 section 名 -> 分片模板路径; AJAX 操作后只返回受影响的分片 HTML
_SECTION_PARTIAL = {
    "menus": "settings/_section_menus.html",
    "types": "settings/_section_types.html",
    "items": "settings/_section_items.html",
    "users": "settings/_section_users.html",
    "import-export": "settings/_section_import_export.html",
    "formula": "settings/_section_formula.html",
    "security": "settings/_section_security.html",
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
        "types": db.session.query(func.count(ItemType.id)).scalar() or 0,
        "assets": db.session.query(func.count(Asset.id)).scalar() or 0,
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
    types = db.session.execute(
        select(ItemType).order_by(ItemType.sort_order, ItemType.id)
    ).scalars().all()
    items = db.session.execute(
        select(AccountItem).order_by(
            AccountItem.type, AccountItem.sort_order, AccountItem.id
        )
    ).scalars().all()
    return {
        "stats": stats, "sys_info": sys_info,
        "users": users, "items": items, "types": types,
        "menu_tree": _build_menu_tree(),
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
            "item_ids": m.item_ids or "",
            "parsed_item_ids": m.parsed_item_ids(),
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
    # 密码可留空 (不门禁); 填了则校验长度与一致性
    has_password = bool(password)
    if has_password:
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
        password_hash=auth.hash_password(password) if has_password else "",
    )
    db.session.add(user)
    if is_default:
        db.session.query(User).filter(User.id != user.id).update(
            {"is_default": False}
        )
    db.session.commit()
    msg = f"已创建用户: {name}"
    msg += " (已配置访问密码)" if has_password else " (无密码, 不门禁)"
    return _done(
        True, msg,
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


@bp.route("/users/<int:user_id>/rename", methods=["POST"])
def user_rename(user_id):
    """修改用户名 — 需验证该用户密码; 级联更新 AccountItem.owner

    数据关联: Asset 通过 user_id 关联用户, 改名后数据自动跟随 (无需迁移);
    AccountItem.owner 为字符串属主, 若其值等于旧用户名则同步更新为新名,
    保证"导入时用用户名作属主"的记录在改名后仍正确归属同一用户。
    改名成功后返回 reload 标记, 前端整页刷新以更新顶栏用户名。
    """
    user = db.session.get(User, user_id)
    if not user:
        return _done(False, "用户不存在")
    new_name = request.form.get("name", "").strip()
    if not new_name:
        return _done(False, "新用户名不能为空")
    if new_name == user.name:
        return _done(False, "新用户名与当前相同")
    dup = db.session.execute(
        select(User).where(User.name == new_name, User.id != user_id)
    ).scalars().first()
    if dup:
        return _done(False, f"用户名 '{new_name}' 已被占用")
    # 密码验证: 有密码用户必须校验, 无密码用户允许空密码直接改名
    password = request.form.get("password", "")
    if user.password_hash:
        if not auth.verify_password(password, user.password_hash):
            return _done(False, "密码错误, 无法改名")
    old_name = user.name
    user.name = new_name
    # 级联更新: 凡 owner 等于旧用户名的条目, 同步改为新名 (其它属主如"家庭"不动)
    updated_owner = db.session.query(AccountItem).filter(
        AccountItem.owner == old_name
    ).update({AccountItem.owner: new_name})
    db.session.commit()
    msg = f"已将用户 '{old_name}' 改名为 '{new_name}'"
    if updated_owner:
        msg += f", 同步更新 {updated_owner} 条目属主"
    ctx = _ctx()
    if _is_ajax():
        return jsonify({
            "ok": True, "msg": msg,
            "sections": _render_sections(ctx, "users", "sysinfo"),
            "reload": True,
        })
    flash(msg, "success")
    return redirect(url_for("settings.index", uid=user_id))


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


# -------------------------------------------------------------- 账户类型管理
@bp.route("/types/add", methods=["POST"])
def type_add():
    """新增账目类型 (如 投资/储蓄/负债)"""
    name = request.form.get("name", "").strip()
    if not name:
        return _done(False, "类型名称不能为空")
    existing = db.session.execute(
        select(ItemType).where(ItemType.name == name)
    ).scalars().first()
    if existing:
        return _done(False, f"类型 '{name}' 已存在")
    max_order = db.session.execute(
        select(func.max(ItemType.sort_order))
    ).scalar() or 0
    db.session.add(ItemType(name=name, sort_order=max_order + 1, is_active=True))
    db.session.commit()
    return _done(
        True, f"已新增类型: {name}",
        ("types", "items", "menus", "formula", "sysinfo"),
    )


@bp.route("/types/<int:type_id>/edit", methods=["POST"])
def type_edit(type_id):
    """编辑类型名称 (会级联更新所有引用此类型的 AccountItem.type)"""
    it = db.session.get(ItemType, type_id)
    if not it:
        return _done(False, "类型不存在")
    new_name = request.form.get("name", "").strip()
    if not new_name:
        return _done(False, "类型名称不能为空")
    if new_name != it.name:
        dup = db.session.execute(
            select(ItemType).where(
                ItemType.name == new_name, ItemType.id != type_id
            )
        ).scalars().first()
        if dup:
            return _done(False, f"类型名称 '{new_name}' 已被占用")
        # 级联更新 AccountItem.type
        old_name = it.name
        db.session.query(AccountItem).filter(
            AccountItem.type == old_name
        ).update({AccountItem.type: new_name})
        it.name = new_name
    it.sort_order = request.form.get("sort_order", it.sort_order, type=int)
    it.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return _done(
        True, f"已更新类型: {it.name}",
        ("types", "items", "menus", "formula"),
    )


@bp.route("/types/<int:type_id>/delete", methods=["POST"])
def type_delete(type_id):
    """删除类型 (若有 AccountItem 引用则阻止)"""
    it = db.session.get(ItemType, type_id)
    if not it:
        return _done(False, "类型不存在")
    ref_count = db.session.execute(
        select(func.count(AccountItem.id)).where(AccountItem.type == it.name)
    ).scalar() or 0
    if ref_count > 0:
        return _done(
            False, f"类型 '{it.name}' 仍被 {ref_count} 个条目引用, "
            f"请先迁移或删除这些条目"
        )
    name = it.name
    db.session.delete(it)
    db.session.commit()
    return _done(
        True, f"已删除类型: {name}",
        ("types", "items", "menus", "formula", "sysinfo"),
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


# -------------------------------------------------------------- 菜单管理
def _parse_item_ids(form) -> str:
    """从表单 checkbox (name=item_ids, value=ID) 收集选中条目 ID, 返回逗号串"""
    ids = form.getlist("item_ids")
    return ",".join(i for i in ids if i.isdigit())


@bp.route("/menus/add", methods=["POST"])
def menu_add():
    name = request.form.get("name", "").strip()
    if not name:
        return _done(False, "菜单名称不能为空")
    parent_id = request.form.get("parent_id", type=int)
    item_ids = _parse_item_ids(request.form)
    sort_order = request.form.get("sort_order", 0, type=int)
    mi = MenuItem(
        name=name, parent_id=parent_id if parent_id else None,
        item_ids=item_ids,
        sort_order=sort_order, is_active=True,
    )
    db.session.add(mi)
    db.session.commit()
    n = len(item_ids.split(",")) if item_ids else 0
    return _done(True, f"已添加菜单: {name} (含 {n} 个条目)", ("menus",))


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
    mi.item_ids = _parse_item_ids(request.form)
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


# -------------------------------------------------------------- CSV 写入工具
def _write_csv(rows: list[dict], fieldnames: list[str]) -> io.BytesIO:
    """把 dict 列表写成 UTF-8 BOM CSV (Excel 友好)"""
    buf = io.BytesIO()
    buf.write(b"\xef\xbb\xbf")  # UTF-8 BOM, 让 Excel 正确识别中文
    text_buf = io.TextIOWrapper(buf, encoding="utf-8", newline="", write_through=True)
    writer = csv.DictWriter(text_buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    text_buf.detach()
    buf.seek(0)
    return buf


# -------------------------------------------------------------- 模板下载 (CSV)
@bp.route("/template/items/csv")
def download_template_csv():
    """下载账户条目模板 (CSV)

    表头: name,type,owner,note,sort_order,is_active
    填好后通过 /import/csv 上传, 系统按表头自动识别为条目结构。
    """
    rows = [
        {"name": "工资", "type": "收入", "owner": "家庭",
         "note": "月度工资收入", "sort_order": 0, "is_active": "true"},
        {"name": "餐饮", "type": "支出", "owner": "家庭",
         "note": "日常饮食", "sort_order": 1, "is_active": "true"},
        {"name": "现金结余", "type": "储蓄", "owner": "家庭",
         "note": "月末现金", "sort_order": 2, "is_active": "true"},
    ]
    buf = _write_csv(rows, ["name", "type", "owner", "note",
                            "sort_order", "is_active"])
    return send_file(
        buf, mimetype="text/csv", as_attachment=True,
        download_name="account_items_template.csv",
    )


@bp.route("/template/history/csv")
def download_history_template_csv():
    """下载历史月度数据模板 (CSV 长表)

    表头: year,month,type,owner,name,value,note
    每行一条月度记录, 填好后通过 /import/csv 上传, 系统按表头自动识别为历史数据。
    """
    rows = [
        {"year": 2024, "month": 1, "type": "收入", "owner": "家庭",
         "name": "工资", "value": 15000.00, "note": "1月工资"},
        {"year": 2024, "month": 1, "type": "支出", "owner": "家庭",
         "name": "餐饮", "value": 3200.50, "note": ""},
        {"year": 2024, "month": 1, "type": "储蓄", "owner": "家庭",
         "name": "现金结余", "value": 50000.00, "note": "月末"},
        {"year": 2024, "month": 2, "type": "收入", "owner": "家庭",
         "name": "工资", "value": 15000.00, "note": ""},
        {"year": 2024, "month": 2, "type": "支出", "owner": "家庭",
         "name": "房租房贷", "value": 4500.00, "note": ""},
        {"year": 2024, "month": 2, "type": "支出", "owner": "家庭",
         "name": "餐饮", "value": 2850.00, "note": ""},
        {"year": 2024, "month": 2, "type": "储蓄", "owner": "家庭",
         "name": "现金结余", "value": 52649.50, "note": ""},
    ]
    buf = _write_csv(rows, ["year", "month", "type", "owner",
                            "name", "value", "note"])
    return send_file(
        buf, mimetype="text/csv", as_attachment=True,
        download_name="history_template.csv",
    )


# -------------------------------------------------------------- CSV 导出
@bp.route("/export/items/csv")
def export_items_csv():
    """导出账户条目结构为 CSV

    表头: name,type,owner,note,sort_order,is_active
    """
    items = db.session.execute(
        select(AccountItem).order_by(AccountItem.type, AccountItem.sort_order)
    ).scalars().all()
    rows = [
        {"name": it.name, "type": it.type, "owner": it.owner,
         "note": it.note or "", "sort_order": it.sort_order,
         "is_active": "true" if it.is_active else "false"}
        for it in items
    ]
    buf = _write_csv(rows, ["name", "type", "owner", "note",
                            "sort_order", "is_active"])
    return send_file(
        buf, mimetype="text/csv", as_attachment=True,
        download_name=f"account_items_{datetime.now().strftime('%Y%m%d')}.csv",
    )


@bp.route("/export/history/csv")
def export_history_csv():
    """导出历史月度数据为 CSV (长表)

    表头: year,month,type,owner,name,value,note
    """
    asset_rows = db.session.execute(
        select(Asset, AccountItem).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).order_by(Asset.year, Asset.month, AccountItem.type)
    ).all()
    rows = [
        {"year": a.year, "month": a.month,
         "type": it.type, "owner": it.owner, "name": it.name,
         "value": float(a.value), "note": a.note or ""}
        for a, it in asset_rows
    ]
    buf = _write_csv(rows, ["year", "month", "type", "owner",
                            "name", "value", "note"])
    return send_file(
        buf, mimetype="text/csv", as_attachment=True,
        download_name=f"history_data_{datetime.now().strftime('%Y%m%d')}.csv",
    )


# -------------------------------------------------------------- CSV 导入
@bp.route("/import/csv", methods=["POST"])
def import_csv():
    """导入 CSV — 按表头自动识别两种结构

    1) 表头含 year,month,value -> 历史月度数据 (长表, 自动找/建 AccountItem, upsert Asset)
    2) 表头含 name,type (无 year/month) -> 账户条目结构

    mode: merge (跳过已存在) / upsert (覆盖已存在)
    """
    file = request.files.get("csv_file")
    if not file or not file.filename:
        return _done(False, "请选择 CSV 文件")
    try:
        raw = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
        fieldnames = reader.fieldnames or []
        records = [dict(r) for r in reader]
    except Exception as e:
        return _done(False, f"CSV 解析失败: {e}")

    if not records:
        return _done(False, "CSV 无数据行")

    mode = request.form.get("import_mode", "merge")
    uid = g.current_user.id

    # ---- 表头识别 ----
    has_ym = "year" in fieldnames and "month" in fieldnames
    has_nt = "name" in fieldnames and "type" in fieldnames
    if has_ym and "value" in fieldnames:
        st = _upsert_assets(records, mode, uid)
        db.session.commit()
        msg = (f"历史数据导入完成: 新增条目 {st['items']} 个, "
               f"新增记录 {st['added']} 条, 更新 {st['updated']} 条")
        if st["skipped"]:
            msg += f", 跳过 {st['skipped']} 条"
        return _done(True, msg, ("items", "formula", "sysinfo"))
    if has_nt:
        added, skipped = _upsert_items(records, mode)
        db.session.commit()
        msg = f"条目结构导入完成: 新增 {added} 条"
        if skipped:
            msg += f", 跳过 {skipped} 条"
        return _done(True, msg, ("items", "formula", "sysinfo"))
    return _done(False, "无法识别的 CSV 表头 (需含 name,type 或 year,month,value)")


def _to_int(v, default=0):
    # 去除千分位逗号 (如 "1,234" -> 1234), 兼容负数
    s = str(v).strip().replace(",", "")
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _to_float(v, default=0.0):
    # 去除千分位逗号 (如 "14,879.37" -> 14879.37, "-8,502.24" -> -8502.24)
    s = str(v).strip().replace(",", "")
    if s == "":
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _to_bool(v, default=True):
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "是"):
        return True
    if s in ("false", "0", "no", "n", "否"):
        return False
    return default


def _upsert_items(records, mode):
    """条目结构 upsert: merge=跳过已存在, upsert=覆盖已存在。返回 (新增, 跳过)"""
    added = skipped = 0
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0
    for it in records:
        name = str(it.get("name") or "").strip()
        itype = str(it.get("type") or "").strip()
        owner = str(it.get("owner") or "家庭").strip()
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
            if mode == "upsert":
                existing.note = str(it.get("note") or existing.note or "")
                existing.sort_order = _to_int(it.get("sort_order"),
                                               existing.sort_order)
                existing.is_active = _to_bool(it.get("is_active"),
                                               existing.is_active)
            else:
                skipped += 1
            continue
        max_order += 1
        db.session.add(AccountItem(
            name=name, type=itype, owner=owner,
            note=str(it.get("note") or ""),
            sort_order=_to_int(it.get("sort_order"), max_order),
            is_active=_to_bool(it.get("is_active"), True),
        ))
        added += 1
    return added, skipped


def _upsert_assets(records, mode, uid):
    """历史月度数据 upsert: merge=跳过已存在, upsert=覆盖。
    返回 {items, added, updated, skipped}"""
    added_items = added_assets = updated_assets = skipped = 0
    max_order = db.session.execute(
        select(func.max(AccountItem.sort_order))
    ).scalar() or 0
    for r in records:
        name = str(r.get("name") or "").strip()
        itype = str(r.get("type") or "").strip()
        owner = str(r.get("owner") or "家庭").strip()
        year_i = _to_int(r.get("year"), 0)
        month_i = _to_int(r.get("month"), 0)
        if not (1 <= month_i <= 12) or year_i <= 0:
            skipped += 1
            continue
        val = _to_float(r.get("value"), None)
        if val is None:
            skipped += 1
            continue
        note = str(r.get("note") or "")
        if not name or not itype:
            skipped += 1
            continue
        # 找/建 AccountItem
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
                name=name, type=itype, owner=owner, note="导入",
                sort_order=max_order, is_active=True,
            )
            db.session.add(item)
            db.session.flush()
            added_items += 1
        # upsert Asset
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
    return {"items": added_items, "added": added_assets,
            "updated": updated_assets, "skipped": skipped}


# JSON 导入导出路由已移除 — 统一走 /import/csv (按表头自动识别 条目结构 / 历史数据)
