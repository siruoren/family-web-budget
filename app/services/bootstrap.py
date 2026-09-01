"""初始化默认条目与账户 - 仅在数据库为空时写入"""
from __future__ import annotations
import os
from flask import current_app
from sqlalchemy import func, select

from .. import db
from ..models import Item, Account, Sheet


DEFAULT_ITEMS = [
    # 收入
    ("收入", "君", "君公司", "君公司工资"),
    ("收入", "君", "君其他", "君其他收入"),
    ("收入", "军", "军公司", "军公司工资"),
    ("收入", "家庭", "公积金提取", "公积金提取"),
    ("收入", "家庭", "理财", "理财收益"),
    ("收入", "家庭", "其他", "其他收入"),
    # 支出
    ("支出", "家庭", "公积金贷款", "公积金贷款"),
    ("支出", "家庭", "物业费", "物业费"),
    ("支出", "家庭", "房租", "房租"),
    ("支出", "家庭", "水费", "水费"),
    ("支出", "家庭", "电费", "电费"),
    ("支出", "家庭", "燃气", "燃气"),
    ("支出", "家庭", "信用支出", "信用合计"),
    ("支出", "家庭", "其他支出", "其他支出"),
]


DEFAULT_ACCOUNTS = [
    # 银行理财 (李)
    ("银行理财", "李", "兴业"),
    ("银行理财", "李", "招行"),
    ("银行理财", "李", "农商1"),
    ("银行理财", "李", "建设银行"),
    ("银行理财", "李", "中行1"),
    ("银行理财", "李", "工商1"),
    # 股市理财
    ("股市理财", "李", "华宝（李）"),
    ("股市理财", "朱", "华宝（朱）"),
    # 流动资金
    ("流动资金", "李", "支付宝1"),
    ("流动资金", "李", "中行2"),
    ("流动资金", "李", "工商2"),
    ("流动资金", "李", "农商2"),
    ("流动资金", "李", "微信"),
    ("流动资金", "朱", "支付宝2"),
    ("流动资金", "朱", "工商银行"),
    ("流动资金", "朱", "其他银行"),
    ("流动资金", "朱", "支付宝"),
    ("流动资金", "朱", "微信"),
    # 外债
    ("外债", "家庭", "外债合计"),
    # 信用
    ("信用", "李", "花呗(李)"),
    ("信用", "李", "广发信(李)"),
    ("信用", "朱", "花呗(朱)"),
    ("信用", "朱", "京东白条"),
]


def ensure_default_items() -> int:
    """仅当 item 表为空时写入默认条目, 返回新增数"""
    if db.session.query(func.count(Item.id)).scalar() > 0:
        return 0
    added = 0
    for i, (cat, owner, sub, name) in enumerate(DEFAULT_ITEMS):
        db.session.add(Item(
            name=name, category=cat, owner=owner, sub_category=sub,
            sort_order=i, is_active=True,
        ))
        added += 1
    db.session.commit()
    return added


def ensure_default_accounts() -> int:
    """仅当 account 表为空时写入默认账户, 返回新增数"""
    if db.session.query(func.count(Account.id)).scalar() > 0:
        return 0
    added = 0
    for i, (typ, owner, name) in enumerate(DEFAULT_ACCOUNTS):
        db.session.add(Account(
            name=name, type=typ, owner=owner, sort_order=i, is_active=True,
        ))
        added += 1
    db.session.commit()
    return added


def ensure_structure_initialized() -> dict | None:
    """首次启动: 若结构缺失则从示例 Excel 初始化

    - Sheet 表为空: 全量初始化 (sheet + 账户 + 条目 + 列结构)
    - Sheet 表已有但 SheetColumn 为空: 仅补列结构 (兼容升级)
    幂等, 重复执行只补齐缺失。
    """
    from ..models import SheetColumn
    sheets_n = db.session.query(func.count(Sheet.id)).scalar()
    cols_n = db.session.query(func.count(SheetColumn.id)).scalar()
    if sheets_n == 0 and cols_n == 0:
        sample = current_app.config.get("SAMPLE_EXCEL") if current_app else None
        if sample is None or not os.path.exists(sample):
            return None
        from .structure import initialize_structure_from_excel
        return initialize_structure_from_excel(str(sample))
    if cols_n == 0:
        sample = current_app.config.get("SAMPLE_EXCEL") if current_app else None
        if sample is None or not os.path.exists(sample):
            return None
        from .structure import _ensure_sheet_columns
        return {"columns_added": _ensure_sheet_columns(str(sample))}
    return None
