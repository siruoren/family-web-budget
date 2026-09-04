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


def last_12m_start(year: int, month: int) -> tuple[int, int]:
    """计算以 (year, month) 为终点的近 12 月窗口起点 (含当月)。

    返回 (yf, mf) 使得 period_series(yf, mf, year, month) 产出 12 个数据点。
    """
    start_m = month - 11
    if start_m <= 0:
        return year - 1, 12 + start_m
    return year, start_m


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
        "savings_total": round(totals.get("储蓄", 0), 2),
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
        sav = by_pcat.get((y, m, "储蓄"), 0)
        out.append({
            "year": y, "month": m, "label": f"{y}-{m:02d}",
            "income": round(inc, 2), "expense": round(exp, 2),
            "balance": round(bal, 2), "savings": round(sav, 2),
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


# -------------------------------------------------------------- 可用年份
def get_available_years(user_id: int) -> list[int]:
    """获取数据库中所有有数据的年份 (降序), 始终包含当前年份。

    用于年月选择器的年份下拉, 取代写死的 current_year ± N 区间。
    """
    rows = db.session.execute(
        select(Asset.year).where(Asset.user_id == user_id).distinct()
    ).scalars().all()
    years = sorted(set(rows), reverse=True)
    now = datetime.now()
    if now.year not in years:
        years.insert(0, now.year)
    return years


# -------------------------------------------------------------- Dashboard 概览
def dashboard_overview(user_id: int, year: int = None,
                        month: int = None) -> dict:
    """首页概览: 指定月份汇总 + 近 12 个月趋势 + Top 项目

    year/month 为 None 时回退到当前年月 (而非最新数据月份)。
    """
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    summary = monthly_summary(year, month, user_id)
    missing = missing_items(year, month, user_id)

    # 近 12 个月趋势
    yf, mf = last_12m_start(year, month)
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


# -------------------------------------------------------------- 月度统计报表
def monthly_report(year: int, month: int, user_id: int) -> dict:
    """月度统计报表: 收支明细 + 资产报表 + 结构占比

    返回:
      - summary: monthly_summary 结果 (按 type 分组明细 + 各 type 合计)
      - by_owner: 按属主分组的合计 (结构占比: 属主维度)
      - by_type: 按类型分组的合计 (结构占比: 类型维度)
      - calc: 双视角公式结果
      - savings_items: 储蓄类条目明细 (用于资产报表)
    """
    from .formula import calculate_month
    summary = monthly_summary(year, month, user_id)
    calc = calculate_month(year, month, user_id)

    # 按属主分组 (应用层解密)
    rows = db.session.execute(
        select(Asset.value_enc, AccountItem.type, AccountItem.owner).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == user_id,
        )
    ).all()
    key = get_current_user_key()
    by_owner: dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    savings_items = []
    for enc, itype, owner in rows:
        val = decrypt_float(enc, key)
        by_owner[owner] += val
        by_type[itype] += val
        if itype == "储蓄":
            savings_items.append((owner, val))

    return {
        "year": year, "month": month,
        "summary": summary, "calc": calc,
        "by_owner": {k: round(v, 2) for k, v in by_owner.items()},
        "by_type": {k: round(v, 2) for k, v in by_type.items()},
        "savings_items": savings_items,
        "total": round(sum(by_type.values()), 2),
    }


# -------------------------------------------------------------- 年度汇总
def annual_report(year: int, user_id: int) -> dict:
    """年度汇总: 年度大盘 + 同比环比 + 年度资产复盘"""
    # 全年逐月 series
    series = period_series(year, 1, year, 12, user_id)

    # 年度合计
    annual_income = round(sum(s["income"] for s in series), 2)
    annual_expense = round(sum(s["expense"] for s in series), 2)
    annual_savings_end = series[-1]["savings"] if series else 0
    annual_savings_start = series[0]["savings"] if series else 0
    annual_net = round(annual_income - annual_expense, 2)
    annual_savings_growth = round(annual_savings_end - annual_savings_start, 2)

    # 同比 (与上一年比)
    last_year = annual_report_simple(year - 1, user_id)
    yoy_income = _pct_change(annual_income, last_year.get("income", 0))
    yoy_expense = _pct_change(annual_expense, last_year.get("expense", 0))
    yoy_savings = _pct_change(annual_savings_end, last_year.get("savings_end", 0))

    # 月度环比 (12 月每月相对上月的变化率)
    mom = []
    for i, s in enumerate(series):
        prev = series[i - 1] if i > 0 else None
        mom.append({
            "label": s["label"],
            "income_mom": _pct_change(s["income"], prev["income"] if prev else 0),
            "expense_mom": _pct_change(s["expense"], prev["expense"] if prev else 0),
            "savings_mom": _pct_change(s["savings"], prev["savings"] if prev else 0),
        })

    return {
        "year": year,
        "series": series,
        "annual_income": annual_income,
        "annual_expense": annual_expense,
        "annual_net": annual_net,
        "annual_savings_end": annual_savings_end,
        "annual_savings_growth": annual_savings_growth,
        "yoy": {
            "income": yoy_income, "expense": yoy_expense,
            "savings": yoy_savings,
            "last_year_income": last_year.get("income", 0),
            "last_year_expense": last_year.get("expense", 0),
            "last_year_savings_end": last_year.get("savings_end", 0),
        },
        "mom": mom,
    }


def annual_report_simple(year: int, user_id: int) -> dict:
    """年度简单汇总 (用于同比基期)"""
    series = period_series(year, 1, year, 12, user_id)
    return {
        "year": year,
        "income": round(sum(s["income"] for s in series), 2),
        "expense": round(sum(s["expense"] for s in series), 2),
        "savings_end": series[-1]["savings"] if series else 0,
    }


def _pct_change(curr: float, prev: float) -> float:
    """百分比变化 (prev=0 时返回 0 避免除零)"""
    if not prev:
        return 0.0
    return round((curr - prev) / abs(prev) * 100, 2)


# -------------------------------------------------------------- 可视化趋势
def trends_report(yf: int, mf: int, yt: int, mt: int, user_id: int) -> dict:
    """可视化趋势: 全维度曲线 + 占比 + 资产波动"""
    series = period_series(yf, mf, yt, mt, user_id)

    # 月度环比波动 (储蓄环比)
    volatility = []
    for i, s in enumerate(series):
        prev = series[i - 1] if i > 0 else None
        sav_prev = prev["savings"] if prev else 0
        vol = round(s["savings"] - sav_prev, 2) if prev else 0
        volatility.append({
            "label": s["label"],
            "savings": s["savings"],
            "savings_change": vol,
            "income": s["income"],
            "expense": s["expense"],
        })

    # 区间内类型占比 (按 type 累计)
    rows = db.session.execute(
        select(AccountItem.type, Asset.value_enc).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).where(
            Asset.user_id == user_id,
        )
    ).all()
    key = get_current_user_key()
    type_share: dict[str, float] = defaultdict(float)
    for itype, enc in rows:
        type_share[itype] += decrypt_float(enc, key)

    # 属主占比
    rows2 = db.session.execute(
        select(AccountItem.owner, Asset.value_enc).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).where(
            Asset.user_id == user_id,
        )
    ).all()
    owner_share: dict[str, float] = defaultdict(float)
    for owner, enc in rows2:
        owner_share[owner] += decrypt_float(enc, key)

    # 储蓄波动率 (标准差 / 均值)
    sav_values = [s["savings"] for s in series]
    sav_avg = sum(sav_values) / max(len(sav_values), 1)
    sav_std = (sum((v - sav_avg) ** 2 for v in sav_values) / max(len(sav_values), 1)) ** 0.5
    sav_volatility_rate = round(sav_std / sav_avg * 100, 2) if sav_avg else 0

    return {
        "yf": yf, "mf": mf, "yt": yt, "mt": mt,
        "series": series,
        "volatility": volatility,
        "type_share": {k: round(v, 2) for k, v in type_share.items()},
        "owner_share": {k: round(v, 2) for k, v in owner_share.items()},
        "sav_avg": round(sav_avg, 2),
        "sav_std": round(sav_std, 2),
        "sav_volatility_rate": sav_volatility_rate,
    }


# -------------------------------------------------------------- 趋势预测
def forecast_report(user_id: int, months_ahead: int = 6) -> dict:
    """趋势预测: 基于历史线性回归外推 + 风险提示 + 资产增长预判"""
    now = datetime.now()
    # 用近 24 个月历史作为回归样本 (数据不足时退化为可用月数)
    yf = now.year - 2
    mf = now.month
    series = period_series(yf, mf, now.year, now.month, user_id)

    # 只取有数据的月份做回归 (过滤全 0 的早期月)
    sample = [s for s in series if s["income"] > 0 or s["expense"] > 0 or s["savings"] > 0]
    if len(sample) < 3:
        return {
            "forecasts": [],
            "risks": [],
            "growth_pred": None,
            "sample_size": len(sample),
            "msg": "历史数据不足 (需 ≥3 个月有数据), 无法预测",
        }

    xs = [float(i) for i in range(len(sample))]
    sav_ys = [s["savings"] for s in sample]
    inc_ys = [s["income"] for s in sample]
    exp_ys = [s["expense"] for s in sample]

    sav_slope, sav_intercept = _linear_regression(xs, sav_ys)
    inc_slope, inc_intercept = _linear_regression(xs, inc_ys)
    exp_slope, exp_intercept = _linear_regression(xs, exp_ys)

    # 外推 N 个月
    forecasts = []
    last_label = sample[-1]["label"]
    for i in range(1, months_ahead + 1):
        x = len(sample) - 1 + i
        # 计算外推月份的 year-month
        y = now.year
        m = now.month + i
        while m > 12:
            m -= 12
            y += 1
        forecasts.append({
            "label": f"{y}-{m:02d}",
            "savings_forecast": round(max(0, sav_slope * x + sav_intercept), 2),
            "income_forecast": round(max(0, inc_slope * x + inc_intercept), 2),
            "expense_forecast": round(max(0, exp_slope * x + exp_intercept), 2),
        })

    # 风险提示
    risks = []
    if inc_slope > 0 and exp_slope > inc_slope:
        risks.append(f"支出增长斜率({exp_slope:.2f}/月) > 收入增长斜率({inc_slope:.2f}/月), 长期将侵蚀储蓄")
    if sav_slope < 0:
        risks.append(f"储蓄呈下降趋势 (斜率 {sav_slope:.2f}/月), 需关注资产缩水")
    if inc_slope < 0:
        risks.append(f"收入呈下降趋势 (斜率 {inc_slope:.2f}/月), 建议开拓收入来源")
    if not risks:
        risks.append("未检测到明显风险, 各项指标走势平稳")

    # 资产增长预判 (6 个月后储蓄预测值 vs 当前)
    current_savings = sample[-1]["savings"]
    future_savings = forecasts[-1]["savings_forecast"] if forecasts else current_savings
    growth_pred = {
        "current": round(current_savings, 2),
        "future_6m": future_savings,
        "delta": round(future_savings - current_savings, 2),
        "growth_rate": _pct_change(future_savings, current_savings),
        "trend": "增长" if future_savings > current_savings else ("下降" if future_savings < current_savings else "持平"),
    }

    return {
        "forecasts": forecasts,
        "risks": risks,
        "growth_pred": growth_pred,
        "sample_size": len(sample),
        "slopes": {
            "savings": round(sav_slope, 2),
            "income": round(inc_slope, 2),
            "expense": round(exp_slope, 2),
        },
        "sample_series": sample,
        "msg": "",
    }
