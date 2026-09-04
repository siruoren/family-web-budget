"""资产计算公式服务 (双视角)

两个独立公式各自计算"本月结余", 差额即为"其他支出":
  储蓄视角: 当月资产总和 - 上月资产总和 = 本月结余
  收支视角: 当月收入 - 当月支出 = 本月结余
  其他支出 = 储蓄视角结余 - 收支视角结余

  - 正值: 资产增长 > 收支净额, 存在未记录的支出 (其他支出)
  - 负值: 资产增长 < 收支净额, 存在未记录的收入 / 多记了支出

异常检测: 上月资产为 0 / 当月资产为 0 但有收支 / 差额绝对值过大

类型体系:
  资产总和 — 月末总资产 (月末结余), 可手动输入; 有值时直接取, 无值时自动计算
  储蓄     — 单项储蓄账户 (现金结余 / 银行卡结余 / ...)
  理财     — 投资理财账户 (股票 / 基金 / 理财产品 / ...)
  自动计算规则: 资产总和 = 有手动值取手动值, 否则 = sum(储蓄) + sum(理财)
"""
from sqlalchemy import select, func
from .. import db
from ..models import Asset, AccountItem, Setting
from .settings import get_setting


# -------------------------------------------------------------- 公式文本
def get_formula() -> str:
    """获取当前公式表达式 (描述性, 供展示)"""
    return get_setting(
        "asset_formula",
        "资产总和(本月)-资产总和(上月)=本月结余(储蓄视角); "
        "当月收入-当月支出=本月结余(收支视角); "
        "差额=其他支出; "
        "资产总和=有手动值取手动值,否则=储蓄+理财",
    )


def set_formula(expr: str):
    """设置公式描述文本 (实际计算逻辑固定为双视角, 文本仅用于展示)"""
    from .settings import set_setting
    set_setting("asset_formula", expr, "str")


# -------------------------------------------------------------- 应用层汇总
def _sum_by_type(year: int, month: int, user_id: int,
                 item_type: str) -> float:
    """汇总指定月份指定类型的所有条目值 (应用层解密累加)

    Asset.value 已加密存储, 无法用 SQL func.sum; 逐行解密累加。
    """
    rows = db.session.execute(
        select(Asset.value_enc).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == user_id,
            AccountItem.type == item_type,
        )
    ).all()
    from .crypto import decrypt_float, get_current_user_key
    key = get_current_user_key()
    return float(sum(decrypt_float(v, key) for (v,) in rows) or 0)


def _has_type_data(year: int, month: int, user_id: int,
                   item_type: str) -> bool:
    """检查指定月份指定类型是否有任何条目数据 (至少一行)"""
    row = db.session.execute(
        select(Asset.id).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == user_id,
            AccountItem.type == item_type,
        ).limit(1)
    ).first()
    return row is not None


def _get_assets_total(year: int, month: int, user_id: int) -> tuple[float, bool]:
    """获取资产总和

    返回 (总值, 是否手动输入):
      - 若 type=资产总和 有条目数据 → 取手动输入值, is_manual=True
      - 否则 → 自动计算 = sum(储蓄) + sum(理财), is_manual=False
    """
    if _has_type_data(year, month, user_id, "资产总和"):
        return _sum_by_type(year, month, user_id, "资产总和"), True
    auto = (_sum_by_type(year, month, user_id, "储蓄")
            + _sum_by_type(year, month, user_id, "理财"))
    return auto, False


def _prev_month(year: int, month: int) -> tuple[int, int]:
    m = month - 1
    if m < 1:
        m = 12
        year -= 1
    return year, m


# -------------------------------------------------------------- 单条目取值
def get_item_value(year: int, month: int, user_id: int,
                   item_id: int) -> float:
    """取指定条目在指定月份的值 (无则返回 0)"""
    from .crypto import decrypt_float, get_current_user_key
    row = db.session.execute(
        select(Asset.value_enc).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == user_id,
            Asset.account_item_id == item_id,
        )
    ).first()
    if not row:
        return 0.0
    return decrypt_float(row[0], get_current_user_key())


def get_last_month_value(year: int, month: int, user_id: int,
                         item_id: int) -> float:
    """取指定条目上月值 (用于储蓄自动结转预填)"""
    py, pm = _prev_month(year, month)
    return get_item_value(py, pm, user_id, item_id)


# -------------------------------------------------------------- 双视角计算
def calculate_month(year: int, month: int, user_id: int) -> dict:
    """计算当月双视角资产公式结果

    返回字段:
      - formula: 公式描述文本
      - savings_total: 当月资产总和 (手动或自动计算)
      - last_savings: 上月资产总和
      - savings_balance: 储蓄视角本月结余 = 当月资产总和 - 上月资产总和
      - income: 当月收入合计
      - expense: 当月支出合计
      - flow_balance: 收支视角本月结余 = 当月收入 - 当月支出
      - other_expense: 其他支出 = 储蓄视角结余 - 收支视角结余
      - savings_items: 储蓄类型条目合计 (现金结余/银行卡结余等)
      - investment_items: 理财类型条目合计
      - is_manual: 当月资产总和是否为手动输入
      - is_manual_last: 上月资产总和是否为手动输入
      - anomaly: 是否数据异常
      - anomaly_msg: 异常描述
      - 兼容旧字段: last_balance(=last_savings), balance(=savings_total),
        expense_items(=expense), calculated_expense(=flow_balance),
        diff(=other_expense)
    """
    py, pm = _prev_month(year, month)

    savings_total, is_manual = _get_assets_total(year, month, user_id)
    last_savings, is_manual_last = _get_assets_total(py, pm, user_id)
    income = _sum_by_type(year, month, user_id, "收入")
    expense = _sum_by_type(year, month, user_id, "支出")
    # 储蓄类型(单项)和理财类型也汇总, 供模板展示
    savings_items = _sum_by_type(year, month, user_id, "储蓄")
    investment_items = _sum_by_type(year, month, user_id, "理财")

    # 双视角
    savings_balance = round(savings_total - last_savings, 2)
    flow_balance = round(income - expense, 2)
    other_expense = round(savings_balance - flow_balance, 2)

    # 异常检测
    anomaly = False
    anomaly_msgs = []

    if last_savings == 0 and savings_total > 0:
        anomaly = True
        anomaly_msgs.append("上月资产总和为 0, 上月数据可能不全")

    if savings_total == 0 and (income > 0 or expense > 0):
        anomaly = True
        anomaly_msgs.append("当月资产总和为 0 但有收支, 请确认是否漏填")

    if abs(other_expense) > 100:
        anomaly = True
        if other_expense > 0:
            anomaly_msgs.append(
                f"储蓄视角结余({savings_balance:.2f}) > 收支视角结余"
                f"({flow_balance:.2f}), 差额 {other_expense:.2f} "
                f"可能存在未记录的支出"
            )
        else:
            anomaly_msgs.append(
                f"储蓄视角结余({savings_balance:.2f}) < 收支视角结余"
                f"({flow_balance:.2f}), 差额 {abs(other_expense):.2f} "
                f"可能存在未记录的收入或支出多记"
            )

    return {
        "formula": get_formula(),
        "year": year, "month": month,
        "savings_total": round(savings_total, 2),
        "last_savings": round(last_savings, 2),
        "savings_balance": savings_balance,
        "income": round(income, 2),
        "expense": round(expense, 2),
        "flow_balance": flow_balance,
        "other_expense": other_expense,
        "savings_items": round(savings_items, 2),
        "investment_items": round(investment_items, 2),
        "is_manual": is_manual,
        "is_manual_last": is_manual_last,
        "anomaly": anomaly,
        "anomaly_msg": "; ".join(anomaly_msgs) if anomaly_msgs else "",
        # ---- 兼容旧模板字段 ----
        "last_balance": round(last_savings, 2),
        "balance": round(savings_total, 2),
        "expense_items": round(expense, 2),
        "calculated_expense": flow_balance,
        "diff": other_expense,
    }


def get_all_types() -> list[str]:
    """获取所有账户条目类型名称 (优先 ItemType 表, 回退 AccountItem distinct)"""
    from ..models import ItemType
    rows = db.session.execute(
        select(ItemType.name).order_by(ItemType.sort_order, ItemType.id)
    ).all()
    if rows:
        return [r[0] for r in rows]
    rows = db.session.execute(
        select(AccountItem.type).distinct().order_by(AccountItem.type)
    ).all()
    return [r[0] for r in rows]
