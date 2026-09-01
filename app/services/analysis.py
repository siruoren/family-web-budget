"""
分析与预测服务

- 月度收支汇总
- 项目趋势时序 (按 Item 聚合)
- 历史各项目趋势与建议
- 未来资产趋势预测 (线性回归 / 月增长率)
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from sqlalchemy import select, func, and_
from .. import db
from ..models import Entry, Item, BalanceSnapshot, Account


# -------------------------------------------------------------- 通用工具
def _periods_range(year_from: int, month_from: int,
                   year_to: int, month_to: int) -> list[tuple[int, int]]:
    """生成 (year, month) 连续序列"""
    out: list[tuple[int, int]] = []
    y, m = year_from, month_from
    while (y, m) <= (year_to, month_to):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


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
def monthly_summary(year: int, month: int) -> dict:
    """指定月份: 收入 / 支出 / 结余明细"""
    entries = db.session.execute(
        select(Entry, Item).join(Item, Entry.item_id == Item.id).where(
            Entry.year == year, Entry.month == month
        )
    ).all()

    income_items: list[dict] = []
    expense_items: list[dict] = []
    income_total = 0.0
    expense_total = 0.0

    for e, it in entries:
        val = float(e.value or 0)
        row = {
            "entry_id": e.id, "item_id": it.id, "name": it.name,
            "owner": it.owner, "sub_category": it.sub_category,
            "value": val, "note": e.note or "", "source": e.source,
        }
        if it.category == "收入":
            income_items.append(row)
            income_total += val
        elif it.category == "支出":
            expense_items.append(row)
            expense_total += val

    net = income_total - expense_total
    return {
        "year": year, "month": month,
        "income_total": round(income_total, 2),
        "expense_total": round(expense_total, 2),
        "net": round(net, 2),
        "income_items": income_items,
        "expense_items": expense_items,
    }


# -------------------------------------------------------------- 趋势时序
def period_series(year_from: int, month_from: int,
                  year_to: int, month_to: int) -> list[dict]:
    """区间内每月: 收入/支出/净额"""
    periods = _periods_range(year_from, month_from, year_to, month_to)
    rows = db.session.execute(
        select(Entry.year, Entry.month, Item.category, func.sum(Entry.value))
        .join(Item, Entry.item_id == Item.id)
        .where(Entry.year >= year_from, Entry.year <= year_to + 1)
        .group_by(Entry.year, Entry.month, Item.category)
    ).all()

    by_period_cat: dict[tuple, float] = {}
    for y, m, cat, val in rows:
        if (y, m) not in periods:
            continue
        by_period_cat[(y, m, cat)] = float(val or 0)

    out = []
    for y, m in periods:
        inc = by_period_cat.get((y, m, "收入"), 0)
        exp = by_period_cat.get((y, m, "支出"), 0)
        out.append({
            "year": y, "month": m, "label": f"{y}-{m:02d}",
            "income": round(inc, 2), "expense": round(exp, 2),
            "net": round(inc - exp, 2),
        })
    return out


# -------------------------------------------------------------- 项目趋势
def item_trend(item_id: int, year_from: int, year_to: int) -> dict:
    """单条目月度时序"""
    item = db.session.get(Item, item_id)
    if not item:
        return {}
    rows = db.session.execute(
        select(Entry.year, Entry.month, Entry.value).where(
            Entry.item_id == item_id,
            Entry.year >= year_from, Entry.year <= year_to,
        ).order_by(Entry.year, Entry.month)
    ).all()
    series = [
        {"year": y, "month": m, "label": f"{y}-{m:02d}",
         "value": float(v or 0)}
        for y, m, v in rows
    ]
    # 趋势线
    xs = [float(i) for i in range(len(series))]
    ys = [p["value"] for p in series]
    slope, intercept = _linear_regression(xs, ys)
    return {
        "item": {
            "id": item.id, "name": item.name, "category": item.category,
            "owner": item.owner, "sub_category": item.sub_category,
        },
        "series": series,
        "slope": round(slope, 2),
        "intercept": round(intercept, 2),
        "trend": "上升" if slope > 0 else ("下降" if slope < 0 else "平稳"),
    }


# -------------------------------------------------------------- 全部项目趋势
def all_item_trends(year_from: int, year_to: int) -> list[dict]:
    items = db.session.execute(
        select(Item).where(Item.is_active == True).order_by(
            Item.category, Item.sort_order
        )
    ).scalars().all()
    out = []
    for it in items:
        trend = item_trend(it.id, year_from, year_to)
        if not trend:
            continue
        # 建议生成
        avg = sum(p["value"] for p in trend["series"]) / max(len(trend["series"]), 1)
        advice = _advice_for_item(trend, avg)
        trend["avg"] = round(avg, 2)
        trend["advice"] = advice
        out.append(trend)
    return out


def _advice_for_item(trend: dict, avg: float) -> str:
    """根据趋势给文字建议"""
    slope = trend["slope"]
    name = trend["item"]["name"]
    cat = trend["item"]["category"]
    if cat == "收入":
        if slope > 0:
            return f"{name} 月均 {avg:.2f} 元且呈上升趋势, 保持稳定收入来源。"
        if slope < 0:
            return f"{name} 月均 {avg:.2f} 元但有下降趋势, 需关注收入稳定性。"
        return f"{name} 月均 {avg:.2f} 元, 趋势平稳。"
    if cat == "支出":
        if slope > 0:
            return f"{name} 月均 {avg:.2f} 元且支出在增长, 建议审视该支出可否压缩。"
        if slope < 0:
            return f"{name} 月均 {avg:.2f} 元且支出在下降, 控制良好。"
        return f"{name} 月均 {avg:.2f} 元, 支出平稳。"
    return f"{name} 月均 {avg:.2f} 元。"


# -------------------------------------------------------------- 资产预测
def forecast_assets(future_months: int = 12) -> dict:
    """
    未来资产趋势预测:
      - 取最近有数据的 N 个月的月末总资产 (BalanceSnapshot 合计) 作为历史
      - 用线性回归 + 月增长率两种方式外推
      - 假设月度净储蓄 = 平均月收入 - 平均月支出
    """
    # 取所有月末总资产时序 (按时间排序), 取最近 12 期
    rows = db.session.execute(
        select(BalanceSnapshot.year, BalanceSnapshot.month,
               func.sum(BalanceSnapshot.value))
        .group_by(BalanceSnapshot.year, BalanceSnapshot.month)
        .order_by(BalanceSnapshot.year, BalanceSnapshot.month)
    ).all()

    all_periods = [
        {"year": y, "month": m, "label": f"{y}-{m:02d}",
         "value": float(val or 0)}
        for y, m, val in rows
    ]
    history = all_periods[-12:] if all_periods else []

    # 如果没有结余数据, 用收入-支出累计模拟
    if not history:
        return {
            "history": [],
            "forecast": [],
            "monthly_net": 0.0,
            "growth_rate": 0.0,
            "slope": None,
            "note": "暂无月末结余数据, 请先导入历史数据或填写结余。",
        }

    last_value = history[-1]["value"]
    xs = [float(i) for i in range(len(history))]
    ys = [h["value"] for h in history]
    slope, intercept = _linear_regression(xs, ys)

    # 月增长率 (几何平均)
    growth = 0.0
    if len(history) >= 2 and history[0]["value"] != 0:
        growth = (history[-1]["value"] / history[0]["value"]) ** (
            1 / (len(history) - 1)
        ) - 1

    # 预测 future_months 个月
    forecast: list[dict] = []
    for i in range(1, future_months + 1):
        x = len(history) - 1 + i
        # 线性外推
        lin_val = slope * x + intercept
        # 复合增长
        grow_val = last_value * ((1 + growth) ** i)
        # 平均值作为最终值 (避免极端)
        final = (lin_val + grow_val) / 2
        # 计算预测月份
        y = history[-1]["year"]
        m = history[-1]["month"] + i
        while m > 12:
            m -= 12
            y += 1
        forecast.append({
            "year": y, "month": m, "label": f"{y}-{m:02d}",
            "value": round(final, 2),
            "linear": round(lin_val, 2),
            "growth": round(grow_val, 2),
        })

    # 平均月净额 (用于提示)
    net_rows = db.session.execute(
        select(Entry.year, Entry.month, Item.category,
               func.sum(Entry.value))
        .join(Item, Entry.item_id == Item.id)
        .group_by(Entry.year, Entry.month, Item.category)
    ).all()
    inc_sum = sum(float(v or 0) for _, _, c, v in net_rows if c == "收入")
    exp_sum = sum(float(v or 0) for _, _, c, v in net_rows if c == "支出")
    n_months = len({(y, m) for y, m, _, _ in net_rows}) or 1
    monthly_net = (inc_sum - exp_sum) / n_months

    return {
        "history": history,
        "forecast": forecast,
        "monthly_net": round(monthly_net, 2),
        "growth_rate": round(growth * 100, 2),
        "slope": round(slope, 2),
        "last_period": history[-1]["label"],
        "history_count": len(history),
    }


# -------------------------------------------------------------- Dashboard 概览
def dashboard_overview() -> dict:
    """首页概览: 最新月份汇总 + 近 12 个月趋势 + Top 项目"""
    # 找到最新有 Entry 数据的月份
    latest_entry = db.session.execute(
        select(Entry).order_by(Entry.year.desc(), Entry.month.desc()).limit(1)
    ).scalars().first()
    # 找到最新有 BalanceSnapshot 数据的月份
    latest_bal = db.session.execute(
        select(BalanceSnapshot).order_by(
            BalanceSnapshot.year.desc(), BalanceSnapshot.month.desc()
        ).limit(1)
    ).scalars().first()

    now = datetime.now()
    # 优先取 Entry 最新月份; 若无则 Balance; 都无则当月
    if latest_entry:
        year, month = latest_entry.year, latest_entry.month
    elif latest_bal:
        year, month = latest_bal.year, latest_bal.month
    else:
        year, month = now.year, now.month

    summary = monthly_summary(year, month)

    # 近 12 个月趋势 (从所选月份回溯)
    y_from = year - 1 if month == 12 else year
    m_from = month + 1 if month < 12 else 1
    if m_from == 1 and month == 12:
        y_from = year
    series = period_series(y_from, m_from, year, month)

    # 总资产 (该月结余快照合计); 若该月无结余, 取最近有结余的月份
    asset_total = db.session.execute(
        select(func.sum(BalanceSnapshot.value)).where(
            BalanceSnapshot.year == year, BalanceSnapshot.month == month
        )
    ).scalar()
    if not asset_total and latest_bal:
        asset_total = db.session.execute(
            select(func.sum(BalanceSnapshot.value)).where(
                BalanceSnapshot.year == latest_bal.year,
                BalanceSnapshot.month == latest_bal.month,
            )
        ).scalar() or 0
    asset_total = float(asset_total or 0)

    # 各条目年度累计 Top
    annual = db.session.execute(
        select(Item.name, Item.category, Item.owner, func.sum(Entry.value))
        .join(Item, Entry.item_id == Item.id)
        .where(Entry.year == year)
        .group_by(Item.id)
        .order_by(func.sum(Entry.value).desc())
        .limit(8)
    ).all()
    top_items = [
        {"name": n, "category": c, "owner": o, "value": float(v or 0)}
        for n, c, o, v in annual
    ]

    return {
        "year": year, "month": month,
        "summary": summary,
        "series": series,
        "asset_total": asset_total,
        "top_items": top_items,
    }
