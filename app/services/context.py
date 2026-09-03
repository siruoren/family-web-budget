"""模板上下文注入 - 用户管理 / 侧边栏 / 月份选择 / 全局变量

v2 架构: 用户由 URL (?uid=) 决定, 侧边栏由 MenuItem 多级菜单驱动
"""
from datetime import datetime
from flask import g, session, url_for, request
from sqlalchemy import select, func

from .. import db
from ..models import User, AccountItem, MenuItem


def ensure_user():
    """从 URL (?uid=N) 提取当前用户; 否则回退 session / 默认用户"""
    uid_arg = request.args.get("uid", type=int)
    if uid_arg:
        user = db.session.get(User, uid_arg)
        if user:
            g.current_user = user
            session["last_user_id"] = user.id
            return
    if hasattr(g, "current_user") and g.current_user:
        return
    last_uid = session.get("last_user_id")
    if last_uid:
        user = db.session.get(User, last_uid)
        if user:
            g.current_user = user
            return
    user = db.session.execute(
        select(User).where(User.is_default == True).order_by(User.sort_order)
    ).scalars().first()
    if not user:
        user = db.session.execute(
            select(User).order_by(User.sort_order, User.id)
        ).scalars().first()
    if not user:
        user = User(name="家庭", is_default=True, sort_order=0)
        db.session.add(user)
        db.session.commit()
    g.current_user = user


def get_current_user() -> User:
    if not hasattr(g, "current_user") or not g.current_user:
        ensure_user()
    return g.current_user


def get_all_users() -> list:
    return db.session.execute(
        select(User).order_by(User.sort_order, User.id)
    ).scalars().all()


def _build_sidebar() -> list:
    """从 MenuItem 表构建多层级侧边菜单树

    递归构建: 每个节点包含 name, url, count, children
    叶子节点(有 filter)的 url 指向 /entries?type=&owner=
    """
    try:
        roots = db.session.execute(
            select(MenuItem).where(
                MenuItem.parent_id.is_(None),
                MenuItem.is_active == True,  # noqa: E712
            ).order_by(MenuItem.sort_order, MenuItem.id)
        ).scalars().all()
    except Exception:
        return []

    def _build_node(node) -> dict:
        children = db.session.execute(
            select(MenuItem).where(
                MenuItem.parent_id == node.id,
                MenuItem.is_active == True,  # noqa: E712
            ).order_by(MenuItem.sort_order, MenuItem.id)
        ).scalars().all()

        child_list = [_build_node(c) for c in children]

        has_filter = bool(node.filter_type or node.filter_owner)
        if has_filter:
            params = {}
            if node.filter_type:
                params["type"] = node.filter_type
            if node.filter_owner:
                params["owner"] = node.filter_owner
            url = url_for("entries.index", **params)
        else:
            url = ""

        count = 0
        if has_filter:
            q = select(func.count(AccountItem.id)).where(
                AccountItem.is_active == True  # noqa: E712
            )
            if node.filter_type:
                q = q.where(AccountItem.type == node.filter_type)
            if node.filter_owner:
                q = q.where(AccountItem.owner == node.filter_owner)
            count = db.session.execute(q).scalar() or 0

        return {
            "id": node.id,
            "name": node.name,
            "url": url,
            "count": count,
            "has_filter": has_filter,
            "icon": node.icon or "",
            "children": child_list,
        }

    return [_build_node(r) for r in roots]


def inject_globals():
    now = datetime.now()
    sel_year = session.get("sel_year", now.year)
    sel_month = session.get("sel_month", now.month)

    user = get_current_user()

    if not hasattr(g, "_sidebar"):
        g._sidebar = _build_sidebar()

    users = get_all_users()

    return {
        "current_year": now.year,
        "current_month": now.month,
        "sel_year": sel_year,
        "sel_month": sel_month,
        "period_label": f"{sel_year}年{sel_month}月",
        "current_user": user,
        "current_user_id": user.id if user else 0,
        "current_user_name": user.name if user else "未命名",
        "all_users": users,
        "sidebar_tree": g._sidebar,
    }
