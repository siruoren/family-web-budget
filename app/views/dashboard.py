"""Dashboard 视图 - 首页概览"""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, g
from sqlalchemy import select, func

from .. import db
from ..models import Asset, AccountItem
from ..services.analysis import dashboard_overview, period_series, get_available_years
from ..services.formula import calculate_month

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    """首页 Dashboard - 概览 + 趋势图 + Top 项目

    默认当前年月; 可通过 ?year=&month= 切换, 年份下拉取数据库所有年份。
    """
    now = datetime.now()
    year = request.args.get("year", now.year, type=int)
    month = request.args.get("month", now.month, type=int)
    uid = g.current_user.id
    overview = dashboard_overview(user_id=uid, year=year, month=month)
    calc = calculate_month(year, month, uid)
    available_years = get_available_years(uid)
    pie_data = overview["summary"]["groups"]
    return render_template(
        "dashboard/index.html", overview=overview, calc=calc,
        sel_year=year, sel_month=month, available_years=available_years,
        pie_data=pie_data,
    )


@bp.route("/api/dashboard")
def api_dashboard():
    return jsonify(dashboard_overview(user_id=g.current_user.id))


@bp.route("/api/series")
def api_series():
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
    return jsonify({"series": period_series(y_from, m_from, y_to, m_to, user_id=g.current_user.id)})
