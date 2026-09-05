"""自定义菜单视图 - 点击左侧菜单显示自定义条目组合

用户在系统配置中创建菜单并勾选条目, 点击菜单后此页面显示所选条目的
区间合计 + 增长分析 + 占比图 + 时间范围选择器 + 逐月趋势。
"""
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g,
)
from sqlalchemy import select

from .. import db
from ..models import AccountItem, Asset, MenuItem
from ..services.analysis import (
    monthly_summary, period_series, get_available_years, last_12m_start,
)
from ..services.formula import get_all_types
from ..services.crypto import decrypt_float, get_current_user_key

bp = Blueprint("menu", __name__)


def _get_range() -> tuple[int, int, int, int]:
    """从 query 参数获取时间范围 (yf, mf, yt, mt)。

    默认: 近 12 个月 (last_12m_start ~ 当前月)。
    """
    now = datetime.now()
    yt = request.args.get("yt", session.get("sel_yt", now.year), type=int)
    mt = request.args.get("mt", session.get("sel_mt", now.month), type=int)
    yf = request.args.get("yf", session.get("sel_yf"), type=int)
    mf = request.args.get("mf", session.get("sel_mf"), type=int)

    if yf is None or mf is None:
        yf, mf = last_12m_start(yt, mt)

    # 确保 yf-mf <= yt-mt; 否则交换
    if (yf, mf) > (yt, mt):
        yf, mf, yt, mt = yt, mt, yf, mf

    session["sel_yf"] = yf
    session["sel_mf"] = mf
    session["sel_yt"] = yt
    session["sel_mt"] = mt
    return yf, mf, yt, mt


@bp.route("/menu/<int:menu_id>")
def view(menu_id):
    """自定义菜单视图 - 显示菜单选中条目的区间合计 + 增长分析"""
    mi = db.session.get(MenuItem, menu_id)
    if not mi or not mi.is_active:
        flash("菜单不存在或已停用", "error")
        return redirect(url_for("dashboard.index"))

    yf, mf, yt, mt = _get_range()
    uid = g.current_user.id

    item_id_list = mi.parsed_item_ids()
    if not item_id_list:
        flash(f"菜单 '{mi.name}' 未选择任何条目, 请到系统配置中编辑", "warning")
        return redirect(url_for("settings.index") + "#menus")

    # 查询选中的条目
    items = db.session.execute(
        select(AccountItem).where(
            AccountItem.id.in_(item_id_list),
            AccountItem.is_active == True,  # noqa: E712
        ).order_by(AccountItem.type, AccountItem.sort_order, AccountItem.id)
    ).scalars().all()

    # 保持菜单中勾选的顺序
    order_map = {iid: i for i, iid in enumerate(item_id_list)}
    items.sort(key=lambda it: order_map.get(it.id, 999))

    # 区间内逐月合计 (趋势图 + 增长分析)
    series = _menu_series(yf, mf, yt, mt, uid, item_id_list)

    # 增长分析
    growth = _growth_analysis(series)

    # 区间内条目占比数据 (item-level, 按 type 分组, 全区间累计)
    pie_data = _range_pie_data(yf, mf, yt, mt, uid, item_id_list)

    # 各条目区间均值/合计
    item_stats = _item_stats(items, uid, yf, mf, yt, mt)

    # 各条目逐月时序 (供前端按选中字段切换趋势图)
    item_series_map = _item_series_map(items, yf, mf, yt, mt, uid)

    # 终月资产值 (供编辑表使用)
    assets = db.session.execute(
        select(Asset).where(
            Asset.year == yt, Asset.month == mt,
            Asset.user_id == uid,
            Asset.account_item_id.in_(item_id_list),
        )
    ).scalars().all()
    asset_map = {a.account_item_id: a for a in assets}

    key = get_current_user_key()
    total = round(sum(decrypt_float(a.value_enc, key) for a in assets), 2)

    available_years = get_available_years(uid)
    all_types = get_all_types()

    # 年份下拉选项: 数据库年份 + 当前选中区间起止年份 (去重降序)
    year_set = set(available_years) | {yf, yt}
    year_options = sorted(year_set, reverse=True)

    range_label = f"{yf}年{mf}月 ~ {yt}年{mt}月"

    return render_template(
        "menu/view.html",
        menu=mi,
        yf=yf, mf=mf, yt=yt, mt=mt,
        range_label=range_label,
        items=items, asset_map=asset_map,
        series=series, pie_data=pie_data,
        item_stats=item_stats, item_series_map=item_series_map,
        total=total, growth=growth,
        available_years=available_years, all_types=all_types,
        year_options=year_options,
    )


def _growth_analysis(series):
    """对区间内逐月合计做增长分析

    series: [{label, value, ...}, ...] 逐月合计列表
    返回: 首月值 / 末月值 / 绝对增长 / 增长率 / 均值 / 斜率 / 趋势方向
    """
    if not series:
        return {
            "first_value": 0, "last_value": 0, "delta": 0,
            "growth_rate": 0, "avg_monthly": 0, "slope": 0,
            "trend": "无数据", "month_count": 0, "months_with_data": 0,
            "range_total": 0,
        }

    values = [p["value"] for p in series]
    first = values[0]
    last = values[-1]
    delta = round(last - first, 2)
    growth_rate = round(delta / abs(first) * 100, 2) if first else 0.0
    avg = round(sum(values) / max(len(values), 1), 2)
    range_total = round(sum(values), 2)

    # 线性回归斜率 (增长趋势)
    n = len(values)
    if n >= 2:
        xs = [float(i) for i in range(n)]
        sx = sum(xs)
        sy = sum(values)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, values))
        denom = n * sxx - sx * sx
        slope = round((n * sxy - sx * sy) / denom, 2) if denom else 0.0
    else:
        slope = 0.0

    trend = "上升" if slope > 0 else ("下降" if slope < 0 else "平稳")
    months_with_data = len([v for v in values if v > 0])

    return {
        "first_value": first,
        "last_value": last,
        "delta": delta,
        "growth_rate": growth_rate,
        "avg_monthly": avg,
        "slope": slope,
        "trend": trend,
        "month_count": n,
        "months_with_data": months_with_data,
        "range_total": range_total,
    }


def _item_series_map(items, yf, mf, yt, mt, user_id):
    """每个条目区间内逐月时序 {item_id: [{label,value}, ...]}"""
    if not items:
        return {}
    ids = [it.id for it in items]
    rows = db.session.execute(
        select(Asset.account_item_id, Asset.year, Asset.month, Asset.value_enc).where(
            Asset.user_id == user_id,
            Asset.account_item_id.in_(ids),
        )
    ).all()
    key = get_current_user_key()
    periods = _periods_range(yf, mf, yt, mt)
    period_set = set(periods)
    by_item: dict[int, dict[tuple, float]] = {}
    for item_id, y, m, enc in rows:
        if (y, m) not in period_set:
            continue
        val = decrypt_float(enc, key)
        by_item.setdefault(item_id, {})[(y, m)] = val
    out = {}
    for it in items:
        per = by_item.get(it.id, {})
        out[it.id] = [
            {"year": y, "month": m, "label": f"{y}-{m:02d}",
             "value": round(per.get((y, m), 0), 2)}
            for y, m in periods
        ]
    return out


def _menu_series(yf, mf, yt, mt, user_id, item_id_list):
    """区间内每月: 选中条目合计 (应用层解密累加)"""
    periods = _periods_range(yf, mf, yt, mt)
    q = select(
        Asset.year, Asset.month, Asset.value_enc
    ).where(
        Asset.user_id == user_id,
        Asset.account_item_id.in_(item_id_list),
    )
    rows = db.session.execute(q).all()
    key = get_current_user_key()
    by_period = {}
    for y, m, enc in rows:
        val = decrypt_float(enc, key)
        by_period[(y, m)] = by_period.get((y, m), 0.0) + val
    out = []
    for y, m in periods:
        out.append({
            "year": y, "month": m, "label": f"{y}-{m:02d}",
            "value": round(by_period.get((y, m), 0), 2),
        })
    return out


def _periods_range(yf, mf, yt, mt):
    """生成 (year, month) 连续序列"""
    out = []
    y, m = yf, mf
    while (y, m) <= (yt, mt):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _range_pie_data(yf, mf, yt, mt, user_id, item_id_list):
    """区间内选中条目的 item-level 占比数据 (全区间累计), 按 type 分组"""
    periods = _periods_range(yf, mf, yt, mt)
    period_set = set(periods)
    q = select(Asset, AccountItem).join(
        AccountItem, Asset.account_item_id == AccountItem.id
    ).where(
        Asset.user_id == user_id,
        Asset.account_item_id.in_(item_id_list),
    )
    rows = db.session.execute(q).all()
    key = get_current_user_key()
    # 按 item_id 累计区间内所有月份的值
    agg: dict[int, float] = {}
    item_info: dict[int, AccountItem] = {}
    for a, it in rows:
        if (a.year, a.month) not in period_set:
            continue
        val = decrypt_float(a.value_enc, key)
        agg[it.id] = agg.get(it.id, 0.0) + val
        item_info[it.id] = it
    groups = {}
    for item_id, val in agg.items():
        it = item_info[item_id]
        groups.setdefault(it.type, []).append({
            "id": it.id, "name": it.name, "owner": it.owner,
            "value": round(val, 2),
        })
    return groups


def _item_stats(items, user_id, yf, mf, yt, mt):
    """各条目区间内均值/合计"""
    if not items:
        return {}
    ids = [it.id for it in items]
    periods = _periods_range(yf, mf, yt, mt)
    period_set = set(periods)
    rows = db.session.execute(
        select(Asset.account_item_id, Asset.year, Asset.month, Asset.value_enc).where(
            Asset.user_id == user_id,
            Asset.account_item_id.in_(ids),
        )
    ).all()
    key = get_current_user_key()
    agg = {}
    for item_id, y, m, enc in rows:
        if (y, m) not in period_set:
            continue
        val = decrypt_float(enc, key)
        s = agg.setdefault(item_id, {"sum": 0.0, "count": 0})
        s["sum"] += val
        s["count"] += 1
    out = {}
    for item_id, s in agg.items():
        cnt = s["count"] or 1
        out[item_id] = {
            "sum": round(s["sum"], 2),
            "avg": round(s["sum"] / cnt, 2),
            "count": int(s["count"]),
        }
    return out


@bp.route("/menu/<int:menu_id>/save", methods=["POST"])
def save(menu_id):
    """保存自定义菜单页面的条目值 (保存到终月 yt/mt)"""
    mi = db.session.get(MenuItem, menu_id)
    if not mi:
        flash("菜单不存在", "error")
        return redirect(url_for("dashboard.index"))

    # 终月 (编辑表的月份)
    now = datetime.now()
    year = request.form.get("year", type=int) or now.year
    month = request.form.get("month", type=int) or now.month
    uid = g.current_user.id

    # 保留时间范围参数 (保存后跳回原区间)
    yf = request.form.get("yf", type=int)
    mf = request.form.get("mf", type=int)
    yt = request.form.get("yt", type=int) or year
    mt = request.form.get("mt", type=int) or month

    item_id_list = mi.parsed_item_ids()
    existing = db.session.execute(
        select(Asset).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == uid,
            Asset.account_item_id.in_(item_id_list),
        )
    ).scalars().all()
    asset_map = {a.account_item_id: a for a in existing}

    saved = 0
    for item_id in item_id_list:
        raw = request.form.get(f"item_{item_id}", "").strip()
        a = asset_map.get(item_id)
        if not raw:
            if a:
                db.session.delete(a)
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if a:
            a.value = val
            a.note = request.form.get(f"note_{item_id}", "").strip()
        else:
            db.session.add(Asset(
                year=year, month=month, account_item_id=item_id,
                user_id=uid, value=val,
                note=request.form.get(f"note_{item_id}", "").strip(),
                source="manual",
            ))
        saved += 1

    db.session.commit()
    flash(f"已保存 {saved} 条 {year}年{month}月 数据", "success")
    return redirect(url_for("menu.view", menu_id=menu_id,
                             yf=yf, mf=mf, yt=yt, mt=mt))
