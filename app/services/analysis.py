"""分析与统计服务 - 基于 Asset 表

- 月度汇总 (按 AccountItem.type 分组)
- 项目趋势时序
- 区间数据
- Dashboard 概览
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from sqlalchemy import select, func, and_
from .. import db
from ..models import Asset, AccountItem, User
from ..services.crypto import decrypt_float, get_current_user_key


# -------------------------------------------------------------- 通用工具
def _periods_range(yf: int, mf: int, yt: int, mt: int) -> list[tuple[int, int]]:
    """生成 (year, month) 连续序列"""
    out: list[tuple[int, int]] = []
    y, m = yf, mf
    while (y, m) <= (yt, mt):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _prev_month(year: int, month: int) -> tuple[int, int]:
    """上一个月"""
    m = month - 1
    if m < 1:
        m = 12
        year -= 1
    return year, m


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """简单最小二乘 -> (slope, intercept)"""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, ys[0]
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


# -------------------------------------------------------------- 月度汇总
def monthly_summary(year: int, month: int, user_id: int) -> dict:
    """指定月份: 按 AccountItem.type 分组的资产明细"""
    q = select(Asset, AccountItem).join(
        AccountItem, Asset.account_item_id == AccountItem.id
    ).where(
        Asset.year == year, Asset.month == month,
        Asset.user_id == user_id,
    )
    entries = db.session.execute(q).all()

    groups: dict[str, list[dict]] = defaultdict(list)
    totals: dict[str, float] = defaultdict(float)

    for a, it in entries:
        val = float(a.value or 0)
        row = {
            "asset_id": a.id, "item_id": it.id, "name": it.name,
            "type": it.type, "owner": it.owner, "note": it.note,
            "value": val, "asset_note": a.note or "", "source": a.source,
        }
        groups[it.type].append(row)
        totals[it.type] += val

    return {
        "year": year, "month": month,
        "groups": dict(groups),
        "totals": dict(totals),
        "income_total": round(totals.get("收入", 0), 2),
        "expense_total": round(totals.get("支出", 0), 2),
        "balance_total": round(totals.get("结余", 0), 2),
    }


# -------------------------------------------------------------- 未填写提醒
def missing_items(year: int, month: int, user_id: int) -> list[dict]:
    """找出当月未填写的条目"""
    all_items = db.session.execute(
        select(AccountItem).where(AccountItem.is_active == True).order_by(
            AccountItem.type, AccountItem.sort_order, AccountItem.id
        )
    ).scalars().all()

    filled_ids = set(
        r[0] for r in db.session.execute(
            select(Asset.account_item_id).where(
                Asset.year == year, Asset.month == month,
                Asset.user_id == user_id,
            )
        ).all()
    )

    return [
        {"id": it.id, "name": it.name, "type": it.type, "owner": it.owner}
        for it in all_items if it.id not in filled_ids
    ]


# -------------------------------------------------------------- 区间时序
def period_series(yf: int, mf: int, yt: int, mt: int, user_id: int) -> list[dict]:
    """区间内每月: 各 type 合计 (应用层解密累加)"""
    periods = _periods_range(yf, mf, yt, mt)
    # 取区间内全部资产行 (year, month, type, value_enc), 逐行解密后分组
    q = select(
        Asset.year, Asset.month, AccountItem.type, Asset.value_enc
    ).join(
        AccountItem, Asset.account_item_id == AccountItem.id
    ).where(
        Asset.user_id == user_id,
    )
    rows = db.session.execute(q).all()
    key = get_current_user_key()

    by_pcat: dict[tuple, float] = {}
    for y, m, cat, enc in rows:
        val = decrypt_float(enc, key)
        by_pcat[(y, m, cat)] = by_pcat.get((y, m, cat), 0.0) + val

    out = []
    for y, m in periods:
        inc = by_pcat.get((y, m, "收入"), 0)
        exp = by_pcat.get((y, m, "支出"), 0)
        bal = by_pcat.get((y, m, "结余"), 0)
        out.append({
            "year": y, "month": m, "label": f"{y}-{m:02d}",
            "income": round(inc, 2), "expense": round(exp, 2),
            "balance": round(bal, 2),
            "net": round(inc - exp, 2),
        })
    return out


# -------------------------------------------------------------- 单条目趋势
def item_trend(item_id: int, yf: int, mf: int, yt: int, mt: int,
               user_id: int) -> dict:
    """单条目区间月度时序"""
    item = db.session.get(AccountItem, item_id)
    if not item:
        return {}
    q = select(Asset.year, Asset.month, Asset.value_enc).where(
        Asset.account_item_id == item_id,
        Asset.user_id == user_id,
    ).order_by(Asset.year, Asset.month)
    rows = db.session.execute(q).all()
    key = get_current_user_key()

    # 过滤到区间内
    periods = set(_periods_range(yf, mf, yt, mt))
    series = [
        {"year": y, "month": m, "label": f"{y}-{m:02d}",
         "value": decrypt_float(enc, key)}
        for y, m, enc in rows if (y, m) in periods
    ]

    xs = [float(i) for i in range(len(series))]
    ys = [p["value"] for p in series]
    slope, intercept = _linear_regression(xs, ys)
    avg = sum(ys) / max(len(ys), 1)

    return {
        "item": {
            "id": item.id, "name": item.name,
            "type": item.type, "owner": item.owner,
        },
        "series": series,
        "avg": round(avg, 2),
        "sum": round(sum(ys), 2),
        "slope": round(slope, 2),
        "trend": "上升" if slope > 0 else ("下降" if slope < 0 else "平稳"),
    }


# -------------------------------------------------------------- Dashboard 概览
def dashboard_overview(user_id: int, year: int = None,
                        month: int = None) -> dict:
    """首页概览: 最新月份汇总 + 近 12 个月趋势 + Top 项目"""
    now = datetime.now()

    if year is None or month is None:
        latest = db.session.execute(
            select(Asset).where(Asset.user_id == user_id)
            .order_by(Asset.year.desc(), Asset.month.desc()).limit(1)
        ).scalars().first()
        if latest:
            year, month = latest.year, latest.month
        else:
            year, month = now.year, now.month

    summary = monthly_summary(year, month, user_id)
    missing = missing_items(year, month, user_id)

    # 近 12 个月趋势
    py, pm = _prev_month(year, month)
    yf = py - 1 if pm == 12 else py
    mf = pm + 1 if pm < 12 else 1
    series = period_series(yf, mf, year, month, user_id)

    # 年度 Top 条目 (应用层解密累加后排序取前 10)
    tq = select(
        AccountItem.id, AccountItem.name, AccountItem.type,
        AccountItem.owner, Asset.value_enc
    ).join(
        AccountItem, Asset.account_item_id == AccountItem.id
    ).where(
        Asset.year == year, Asset.user_id == user_id,
    )
    rows = db.session.execute(tq).all()
    key = get_current_user_key()
    sums: dict[int, dict] = {}
    for item_id, name, itype, owner, enc in rows:
        val = decrypt_float(enc, key)
        slot = sums.setdefault(item_id, {
            "name": name, "type": itype, "owner": owner, "value": 0.0,
        })
        slot["value"] += val
    top_items = sorted(sums.values(), key=lambda x: x["value"], reverse=True)[:10]

    return {
        "year": year, "month": month,
        "summary": summary,
        "missing": missing,
        "series": series,
        "top_items": top_items,
    }
