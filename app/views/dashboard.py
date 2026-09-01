"""Dashboard 视图 - 首页"""
from flask import Blueprint, render_template, request, jsonify, session, g
from sqlalchemy import select

from .. import db
from ..models import Entry, Item, BalanceSnapshot
from ..services.analysis import (
    dashboard_overview, monthly_summary, period_series, forecast_assets,
)

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    """首页 Dashboard - 概览 + 趋势图 + Top 项目"""
    from datetime import datetime
    now = datetime.now()
    year = request.args.get("year", type=int) or now.year
    month = request.args.get("month", type=int) or now.month
    overview = dashboard_overview(user_id=g.user_id, year=year, month=month)
    return render_template("dashboard/index.html", year=year, month=month, **overview)


@bp.route("/api/dashboard")
def api_dashboard():
    """Dashboard 数据 API (供前端 fetch 重绘图表)"""
    return jsonify(dashboard_overview(user_id=g.user_id))


@bp.route("/api/series")
def api_series():
    """近 N 月趋势序列"""
    from datetime import datetime
    now = datetime.now()
    months = request.args.get("months", 12, type=int)
    y_to, m_to = now.year, now.month
    y_from = y_to - (months // 12)
    m_from = m_to - (months % 12) + 1
    if m_from > 12:
        m_from -= 12
        y_from += 1
    if m_from < 1:
        m_from += 12
        y_from -= 1
    return jsonify({"series": period_series(y_from, m_from, y_to, m_to, user_id=g.user_id)})
