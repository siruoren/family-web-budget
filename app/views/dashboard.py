"""Dashboard 视图 - 首页概览"""
from flask import Blueprint, render_template, request, jsonify, g
from sqlalchemy import select, func

from .. import db
from ..models import Asset, AccountItem
from ..services.analysis import dashboard_overview, period_series
from ..services.formula import calculate_month

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    """首页 Dashboard - 概览 + 趋势图 + Top 项目"""
    overview = dashboard_overview(user_id=g.current_user.id)
    calc = calculate_month(overview["year"], overview["month"], g.current_user.id)
    return render_template("dashboard/index.html", overview=overview, calc=calc)


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
