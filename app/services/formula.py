"""资产计算公式服务

默认公式: 上月结余 + 当月收入 - 当月结余 = 支出
如果支出条目总和不等于结算结果的差额, 默认显示"支出(其他)"的值
当月资产计算各项值不满足公式时, 自动提醒数据异常

支持自定义多账户类型配置 (通过 Setting 表存储公式表达式)
"""
from sqlalchemy import select, func
from .. import db
from ..models import Asset, AccountItem, Setting
from .locking import get_setting


def get_formula() -> str:
    """获取资产计算公式表达式 (默认: 上月结余+当月收入-当月结余=支出)"""
    return get_setting("asset_formula",
                       "上月结余+当月收入-当月结余=支出")


def set_formula(expr: str):
    """设置资产计算公式"""
    from .locking import set_setting
    set_setting("asset_formula", expr, "str")


def _sum_by_type(year: int, month: int, user_id: int,
                 item_type: str) -> float:
    """汇总指定月份指定类型的所有条目值 (应用层解密累加)

    Asset.value 已加密存储, 无法用 SQL func.sum; 改为逐行解密累加。
    """
    rows = db.session.execute(
        select(Asset.value_enc, AccountItem.type).join(
            AccountItem, Asset.account_item_id == AccountItem.id
        ).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == user_id,
            AccountItem.type == item_type,
        )
    ).all()
    from .crypto import decrypt_float, get_current_user_key
    key = get_current_user_key()
    return float(sum(decrypt_float(v, key) for (v, _t) in rows) or 0)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    m = month - 1
    if m < 1:
        m = 12
        year -= 1
    return year, m


def calculate_month(year: int, month: int, user_id: int) -> dict:
    """计算当月资产公式结果

    默认公式: 上月结余 + 当月收入 - 当月结余 = 支出

    返回:
        - formula: 公式文本
        - last_balance: 上月结余合计
        - income: 当月收入合计
        - balance: 当月结余合计
        - expense_items: 当月支出条目合计
        - calculated_expense: 公式计算的支出
        - diff: 支出差额 = 计算支出 - 支出条目合计
        - other_expense: 差额 (正=少填了支出, 负=多填了支出)
        - anomaly: 是否数据异常
        - anomaly_msg: 异常描述
    """
    py, pm = _prev_month(year, month)

    last_balance = _sum_by_type(py, pm, user_id, "结余")
    income = _sum_by_type(year, month, user_id, "收入")
    balance = _sum_by_type(year, month, user_id, "结余")
    expense_items = _sum_by_type(year, month, user_id, "支出")

    # 公式: 上月结余 + 当月收入 - 当月结余 = 支出
    calculated_expense = round(last_balance + income - balance, 2)
    diff = round(calculated_expense - expense_items, 2)

    # 差额 > 0: 支出条目少了 (有未记录的支出)
    # 差额 < 0: 支出条目多了 (超出了公式计算值)
    other_expense = diff  # "支出(其他)" 的值

    # 异常检测
    anomaly = False
    anomaly_msgs = []

    # 如果结余为0但有收入/支出, 可能漏填了结余
    if balance == 0 and (income > 0 or expense_items > 0):
        anomaly = True
        anomaly_msgs.append("当月结余为 0, 请确认是否漏填结余数据")

    # 如果上月结余为0但当月有数据, 可能上月数据不全
    if last_balance == 0 and balance > 0:
        anomaly = True
        anomaly_msgs.append("上月结余为 0, 上月数据可能不全")

    # 如果差额绝对值 > 100, 提醒
    if abs(diff) > 100:
        anomaly = True
        if diff > 0:
            anomaly_msgs.append(
                f"支出条目合计({expense_items:.2f}) < 公式计算支出"
                f"({calculated_expense:.2f}), 差额 {diff:.2f} "
                f"可能存在未记录的支出"
            )
        else:
            anomaly_msgs.append(
                f"支出条目合计({expense_items:.2f}) > 公式计算支出"
                f"({calculated_expense:.2f}), 差额 {abs(diff):.2f} "
                f"请检查支出数据是否正确"
            )

    return {
        "formula": get_formula(),
        "year": year, "month": month,
        "last_balance": round(last_balance, 2),
        "income": round(income, 2),
        "balance": round(balance, 2),
        "expense_items": round(expense_items, 2),
        "calculated_expense": calculated_expense,
        "diff": diff,
        "other_expense": other_expense,
        "anomaly": anomaly,
        "anomaly_msg": "; ".join(anomaly_msgs) if anomaly_msgs else "",
    }


def get_all_types() -> list[str]:
    """获取所有账户条目类型名称 (供公式 datalist / 条目与菜单类型选择)

    优先查 ItemType 表 (用户手动管理的类型); 若表为空 (旧库未迁移),
    回退查 AccountItem.type distinct (兼容)。
    """
    from ..models import ItemType
    rows = db.session.execute(
        select(ItemType.name).order_by(ItemType.sort_order, ItemType.id)
    ).all()
    if rows:
        return [r[0] for r in rows]
    # 回退: 旧库无 ItemType 表数据时从 AccountItem 聚合
    rows = db.session.execute(
        select(AccountItem.type).distinct()
        .order_by(AccountItem.type)
    ).all()
    return [r[0] for r in rows]
