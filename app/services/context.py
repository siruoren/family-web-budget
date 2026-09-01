"""模板上下文注入 - 月份选择器 / 当前周期 / 统计概览 / 并发锁用户标识 / 侧边栏"""
import uuid
from datetime import datetime
from flask import g, session, url_for
from sqlalchemy import select

from .. import db
from ..models import Sheet, Account, SheetColumn


def ensure_user():
    """确保每个会话拥有唯一 user_id (用于并发锁标识)

    首次访问时生成, 并持久化在 session 中.
    user_label 可由用户在 /me/label 设置, 默认用 user_id 前 6 位.
    """
    if "user_id" not in session:
        session["user_id"] = "u_" + uuid.uuid4().hex[:8]
    if "user_label" not in session or not session["user_label"]:
        session["user_label"] = "用户" + session["user_id"][-4:]
    g.user_id = session["user_id"]
    g.user_label = session["user_label"]


def _build_sidebar_tree() -> list:
    """构建左侧多级菜单: Sheet(大菜单) -> 大类 -> 小项(叶子)

    - balances 类: 叶子=账户 (链接到账户编辑), 数据来自 Account.sheet/group
    - entries  类: 叶子=条目列名 (链接到 /sheet/<name> 跳转该年条目),
                  数据来自 SheetColumn (各年度表实际列名)
    - other    类: 叶子菜单 "查看 ›" -> /sheet/<name>
    """
    try:
        sheets = db.session.execute(
            select(Sheet).where(Sheet.is_active == True)  # noqa: E712
            .order_by(Sheet.sort_order, Sheet.id)
        ).scalars().all()
    except Exception:
        return []
    if not sheets:
        return []

    # 结余类账户: 按 sheet+group 分组
    bal_sheet_names = [s.name for s in sheets if s.kind == "balances"]
    accounts_by_sheet: dict = {}
    if bal_sheet_names:
        accs = db.session.execute(
            select(Account).where(
                Account.is_active == True,  # noqa: E712
                Account.sheet.in_(bal_sheet_names),
            ).order_by(Account.sort_order, Account.id)
        ).scalars().all()
        for a in accs:
            accounts_by_sheet.setdefault(a.sheet, []).append(a)

    # 条目类列结构 (SheetColumn): 按 sheet+group 分组
    ent_sheet_names = [s.name for s in sheets if s.kind == "entries"]
    cols_by_sheet: dict = {}
    if ent_sheet_names:
        cols = db.session.execute(
            select(SheetColumn).where(
                SheetColumn.sheet_name.in_(ent_sheet_names)
            ).order_by(SheetColumn.sort_order, SheetColumn.id)
        ).scalars().all()
        for c in cols:
            cols_by_sheet.setdefault(c.sheet_name, []).append(c)

    tree = []
    for s in sheets:
        node = {
            "name": s.name, "kind": s.kind, "order": s.sort_order,
            "groups": [], "count": 0,
            "url": url_for("sheets.detail", name=s.name),
        }
        if s.kind == "balances":
            accs = accounts_by_sheet.get(s.name, [])
            gmap: dict = {}
            gorder: list = []
            for a in accs:
                gname = a.group or a.type or "其他"
                if gname not in gmap:
                    gmap[gname] = []
                    gorder.append(gname)
                gmap[gname].append({
                    "name": a.name,
                    "url": url_for("items.account_edit", acc_id=a.id),
                    "sub": f"{a.owner} · {a.type}",
                })
            for gname in gorder:
                node["groups"].append({"name": gname, "count": len(gmap[gname]),
                                       "leaves": gmap[gname]})
            node["count"] = len(accs)
        elif s.kind == "entries":
            cols = cols_by_sheet.get(s.name, [])
            gmap: dict = {}
            gorder: list = []
            for c in cols:
                if c.group not in gmap:
                    gmap[c.group] = []
                    gorder.append(c.group)
                gmap[c.group].append({
                    "name": c.name,
                    "url": url_for("sheets.detail", name=s.name),
                    "sub": c.item_key.split("|", 1)[0] if c.item_key else "",
                })
            for gname in gorder:
                node["groups"].append({"name": gname, "count": len(gmap[gname]),
                                       "leaves": gmap[gname]})
            node["count"] = len(cols)
        tree.append(node)
    return tree


def inject_globals():
    now = datetime.now()
    # 用户选择的周期 (从 query / session 读取, 默认当月)
    sel_year = session.get("sel_year", now.year)
    sel_month = session.get("sel_month", now.month)
    return {
        "current_year": now.year,
        "current_month": now.month,
        "sel_year": sel_year,
        "sel_month": sel_month,
        "period_label": f"{sel_year}年{sel_month}月",
        "user_id": g.get("user_id", session.get("user_id", "anonymous")),
        "user_label": g.get(
            "user_label", session.get("user_label", "anonymous")
        ),
        "sidebar_tree": _build_sidebar_tree(),
    }
