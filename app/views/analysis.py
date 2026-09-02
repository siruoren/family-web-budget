"""智能分析视图 - 趋势 / 区间数据 / 公式异常检测

v2 架构: 基于 Asset 表
"""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, session, g
from sqlalchemy import select, func

from .. import db
from ..models import Asset, AccountItem
from ..services.analysis import (
    period_series, item_trend, monthly_summary, _periods_range,
)
from ..services.formula import calculate_month, get_formula, get_all_types

bp = Blueprint("analysis", __name__)


@bp.route("/analysis")
def index():
    """智能分析 - 区间趋势 / 各条目分析 / 公式检测"""
    uid = g.current_user.id
    now = datetime.now()

    # 默认分析近 12 个月
    yf = request.args.get("yf", now.year - 1, type=int)
    mf = request.args.get("mf", now.month, type=int)
    yt = request.args.get("yt", now.year, type=int)
    mt = request.args.get("mt", now.month, type=int)

    # 区间趋势
    series = period_series(yf, mf, yt, mt, uid)

    # 各条目趋势
    items = db.session.execute(
        select(AccountItem).where(AccountItem.is_active == True).order_by(
            AccountItem.type, AccountItem.sort_order, AccountItem.id
        )
    ).scalars().all()

    item_trends = []
    for it in items:
        t = item_trend(it.id, yf, mf, yt, mt, uid)
        if t and t["series"]:
            t["item_id"] = it.id
            item_trends.append(t)

    # 公式计算 (每个月)
    periods = _periods_range(yf, mf, yt, mt)
    monthly_calcs = []
    for y, m in periods:
        c = calculate_month(y, m, uid)
        if c["income"] > 0 or c["expense_items"] > 0 or c["balance"] > 0:
            monthly_calcs.append(c)

    # 异常月份
    anomalies = [c for c in monthly_calcs if c["anomaly"]]

    return render_template(
        "analysis/index.html",
        series=series, item_trends=item_trends,
        monthly_calcs=monthly_calcs, anomalies=anomalies,
        yf=yf, mf=mf, yt=yt, mt=mt,
        formula=get_formula(), all_types=get_all_types(),
    )


@bp.route("/analysis/item/<int:item_id>")
def item_detail(item_id):
    """单条目详细趋势 (JSON)"""
    uid = g.current_user.id
    now = datetime.now()
    yf = request.args.get("yf", now.year - 2, type=int)
    mf = request.args.get("mf", 1, type=int)
    yt = request.args.get("yt", now.year, type=int)
    mt = request.args.get("mt", now.month, type=int)
    trend = item_trend(item_id, yf, mf, yt, mt, uid)
    return jsonify(trend)
