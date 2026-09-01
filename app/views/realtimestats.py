"""实时统计视图 - 银行账户实时统计"""
from flask import Blueprint, render_template, request, jsonify, session, g
from sqlalchemy import select

from .. import db
from ..models import Account, BalanceSnapshot

bp = Blueprint("realtimestats", __name__, url_prefix="/realtimestats")


# 默认项目列表配置
DEFAULT_SAVINGS_ITEMS = [
    "农商银行", "邮政银行", "交通银行", "浦发银行", "华宝", "支付宝", "微信", "京东"
]

DEFAULT_CREDIT_ITEMS = [
    "广发银行", "工商银行", "招商银行", "交通银行", "花呗", "京东"
]


@bp.route("/")
def index():
    """实时统计首页"""
    # 获取当前用户选择的收藏项目
    user_favorites = session.get("realtimestats_favorites", {})
    
    savings_favorites = user_favorites.get("savings", DEFAULT_SAVINGS_ITEMS)
    credit_favorites = user_favorites.get("credit", DEFAULT_CREDIT_ITEMS)
    
    # 获取所有账户
    accounts = db.session.execute(
        select(Account).where(Account.is_active == True).order_by(Account.type, Account.name)
    ).scalars().all()
    
    # 按类型分组账户
    savings_accounts = [a for a in accounts if a.type in ("银行理财", "流动资金")]
    credit_accounts = [a for a in accounts if a.type in ("信用", "外债")]
    
    return render_template(
        "realtimestats/index.html",
        savings_favorites=savings_favorites,
        credit_favorites=credit_favorites,
        savings_accounts=savings_accounts,
        credit_accounts=credit_accounts,
        all_savings_items=DEFAULT_SAVINGS_ITEMS,
        all_credit_items=DEFAULT_CREDIT_ITEMS,
    )


@bp.route("/save-favorites", methods=["POST"])
def save_favorites():
    """保存用户收藏的项目"""
    savings_items = request.form.getlist("savings_items[]")
    credit_items = request.form.getlist("credit_items[]")
    
    user_favorites = {
        "savings": savings_items if savings_items else DEFAULT_SAVINGS_ITEMS,
        "credit": credit_items if credit_items else DEFAULT_CREDIT_ITEMS,
    }
    
    session["realtimestats_favorites"] = user_favorites
    
    return jsonify({"success": True, "favorites": user_favorites})


@bp.route("/get-accounts-data")
def get_accounts_data():
    """获取账户数据"""
    # 获取最新月份的结余数据
    latest_snapshot = db.session.execute(
        select(BalanceSnapshot).order_by(
            BalanceSnapshot.year.desc(), 
            BalanceSnapshot.month.desc()
        ).limit(1)
    ).scalars().first()
    
    if not latest_snapshot:
        return jsonify({"error": "暂无数据"})
    
    year = latest_snapshot.year
    month = latest_snapshot.month
    
    # 获取该月所有结余数据
    snapshots = db.session.execute(
        select(BalanceSnapshot).where(
            BalanceSnapshot.year == year,
            BalanceSnapshot.month == month,
            BalanceSnapshot.user_id == g.user_id
        )
    ).scalars().all()
    
    # 构建账户数据字典
    account_data = {}
    for snap in snapshots:
        account_data[snap.account_id] = {
            "value": float(snap.value or 0),
            "note": snap.note or ""
        }
    
    # 获取所有账户信息
    accounts = db.session.execute(
        select(Account).where(Account.is_active == True)
    ).scalars().all()
    
    result = []
    for acc in accounts:
        data = account_data.get(acc.id, {"value": 0, "note": ""})
        result.append({
            "id": acc.id,
            "name": acc.name,
            "type": acc.type,
            "owner": acc.owner,
            "value": data["value"],
            "note": data["note"]
        })
    
    return jsonify({
        "year": year,
        "month": month,
        "accounts": result
    })