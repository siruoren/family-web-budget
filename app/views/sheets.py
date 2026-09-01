"""工作表视图 - 左侧大菜单点击后的 sheet 详情页

  /sheet/<name>  : 展示该工作表的结构与数据
    - balances 类: 按大类分组列出账户 + 最新月结余
    - entries 类 : 提示跳转月度条目
    - other 类   : 结构待解析提示
"""
from urllib.parse import quote
from flask import (
    Blueprint, render_template, abort, redirect, url_for,
)
from sqlalchemy import select, func, desc

from .. import db
from ..models import Sheet, Account, BalanceSnapshot

bp = Blueprint("sheets", __name__)


@bp.route("/sheet/<path:name>")
def detail(name: str):
    sheet = db.session.execute(
        select(Sheet).where(Sheet.name == name)
    ).scalars().first()
    if not sheet:
        # 该 sheet 未登记 -> 提示去初始化结构
        return render_template("sheets/missing.html", name=name)

    ctx = {"sheet": sheet}

    if sheet.kind == "balances":
        accs = db.session.execute(
            select(Account).where(
                Account.sheet == name, Account.is_active == True  # noqa: E712
            ).order_by(Account.sort_order, Account.id)
        ).scalars().all()
        # 最新月份
        latest = db.session.execute(
            select(BalanceSnapshot.year, BalanceSnapshot.month)
            .order_by(desc(BalanceSnapshot.year), desc(BalanceSnapshot.month))
            .limit(1)
        ).first()
        latest_year = latest[0] if latest else None
        latest_month = latest[1] if latest else None
        # 取每个账户最新月结余
        value_map: dict[int, float] = {}
        if latest_year:
            snaps = db.session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.year == latest_year,
                    BalanceSnapshot.month == latest_month,
                    BalanceSnapshot.account_id.in_([a.id for a in accs]),
                )
            ).scalars().all()
            for s in snaps:
                value_map[s.account_id] = float(s.value or 0)
        # 大类分组
        groups: dict[str, list] = {}
        order: list[str] = []
        for a in accs:
            g = a.group or a.type or "其他"
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append((a, value_map.get(a.id)))
        ctx.update(groups_order=order, groups=groups,
                   latest_year=latest_year, latest_month=latest_month)
        return render_template("sheets/balances.html", **ctx)

    if sheet.kind == "entries":
        # 年度账单 -> 跳到月度条目页 (按工作表名里的年份)
        import re
        m = re.search(r"(20\d{2})", name)
        if m:
            return redirect(url_for("entries.index", year=int(m.group(1))))
        return render_template("sheets/other.html", **ctx)

    return render_template("sheets/other.html", **ctx)
