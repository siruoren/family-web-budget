"""报表模块 - 月度统计 / 年度汇总 / 可视化趋势 / 趋势预测

四个独立报表页, 全部基于 Asset 表, 应用层解密。
"""
from datetime import datetime
from flask import Blueprint, render_template, request, session, g

from ..services.analysis import (
    monthly_report, annual_report, trends_report, forecast_report,
    get_available_years,
)

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _period() -> tuple[int, int]:
    now = datetime.now()
    year = request.args.get("year", session.get("sel_year", now.year), type=int)
    month = request.args.get("month", session.get("sel_month", now.month), type=int)
    session["sel_year"] = year
    session["sel_month"] = month
    return year, month


@bp.route("/monthly")
def monthly():
    """月度统计报表: 收支明细 + 资产报表 + 结构占比"""
    uid = g.current_user.id
    year, month = _period()
    data = monthly_report(year, month, uid)
    years = get_available_years(uid)
    return render_template(
        "reports/monthly.html", data=data,
        available_years=years, year=year, month=month,
    )


@bp.route("/annual")
def annual():
    """年度汇总: 大盘 + 同比环比 + 资产复盘"""
    uid = g.current_user.id
    now = datetime.now()
    year = request.args.get("year", now.year, type=int)
    data = annual_report(year, uid)
    years = get_available_years(uid)
    return render_template(
        "reports/annual.html", data=data, available_years=years,
    )


@bp.route("/trends")
def trends():
    """可视化趋势: 全维度曲线 + 占比 + 资产波动"""
    uid = g.current_user.id
    now = datetime.now()
    yf = request.args.get("yf", now.year - 1, type=int)
    mf = request.args.get("mf", now.month, type=int)
    yt = request.args.get("yt", now.year, type=int)
    mt = request.args.get("mt", now.month, type=int)
    data = trends_report(yf, mf, yt, mt, uid)
    return render_template("reports/trends.html", data=data)


@bp.route("/forecast")
def forecast():
    """趋势预测: 线性回归外推 + 风险提示 + 资产增长预判"""
    uid = g.current_user.id
    months_ahead = request.args.get("months", 6, type=int)
    data = forecast_report(uid, months_ahead)
    return render_template("reports/forecast.html", data=data)
