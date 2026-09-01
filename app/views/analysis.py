"""分析视图 - 项目趋势 / 历史项目建议 / 未来资产预测"""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, g

from ..services.analysis import (
    item_trend, all_item_trends, forecast_assets, period_series,
)

bp = Blueprint("analysis", __name__)


@bp.route("/analysis")
def index():
    now = datetime.now()
    year_from = request.args.get("year_from", now.year - 2, type=int)
    year_to = request.args.get("year_to", now.year, type=int)
    return render_template(
        "analysis/index.html",
        year_from=year_from, year_to=year_to,
    )


@bp.route("/api/analysis/trends")
def api_trends():
    year_from = request.args.get("year_from", type=int)
    year_to = request.args.get("year_to", type=int)
    return jsonify({"trends": all_item_trends(year_from, year_to, user_id=g.user_id)})


@bp.route("/api/analysis/item/<int:item_id>")
def api_item_trend(item_id: int):
    year_from = request.args.get("year_from", type=int)
    year_to = request.args.get("year_to", type=int)
    return jsonify(item_trend(item_id, year_from, year_to, user_id=g.user_id))


@bp.route("/api/analysis/forecast")
def api_forecast():
    months = request.args.get("months", 12, type=int)
    return jsonify(forecast_assets(future_months=months, user_id=g.user_id))


@bp.route("/api/analysis/series")
def api_series():
    now = datetime.now()
    y_to = request.args.get("year_to", now.year, type=int)
    m_to = request.args.get("month_to", now.month, type=int)
    y_from = request.args.get("year_from", now.year - 1, type=int)
    m_from = request.args.get("month_from", now.month, type=int)
    return jsonify({
        "series": period_series(y_from, m_from, y_to, m_to, user_id=g.user_id),
    })
