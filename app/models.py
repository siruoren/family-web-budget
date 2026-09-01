"""
数据模型 (M) - SQLite 持久化

核心实体:
  Item           : 账单条目模板 (君公司 / 军公司 / 房租 / 水费 ...)
  Entry          : 月度条目记录 (year + month + item 唯一, 用于去重)
  Account        : 结余账户 (银行理财 / 股市 / 流动资金 / 外债)
  BalanceSnapshot: 月度账户结余快照
  ImportLog      : 数据导入历史 (来源 / 月份范围 / 命中/去重行数)
  EditLock       : 并发编辑锁 (条目/账户级, 3 分钟自动释放)
  Setting        : 键值配置 (锁TTL / 全局开关 等)
"""
from datetime import datetime
from sqlalchemy import (
    Integer, String, Numeric, ForeignKey, DateTime, Boolean, Text,
    UniqueConstraint, Index, func
)
from sqlalchemy.orm import relationship

from . import db


class Sheet(db.Model):
    """Excel 工作表登记表 - 驱动左侧多级菜单 (支持无限层级)

    name      : Excel 工作表名 (同级唯一，与parent_id组合唯一)
    kind      : entries(年度账单) / balances(家庭结余) / other(其他)
    sort_order: 工作表在同级中的顺序
    parent_id : 父菜单ID，NULL表示顶级菜单
    level     : 层级深度 (0=顶级, 1=二级, ...)
    """
    __tablename__ = "sheet"
    __table_args__ = (
        UniqueConstraint("name", "parent_id", name="uq_sheet_name_parent"),
        Index("ix_sheet_name", "name"),
    )

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(128), nullable=False, index=True)
    kind = db.Column(String(16), default="other")
    sort_order = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, index=True)
    source_file = db.Column(String(255), default="")
    note = db.Column(String(255), default="")
    imported_at = db.Column(DateTime, default=datetime.utcnow)
    parent_id = db.Column(Integer, ForeignKey('sheet.id', ondelete='CASCADE'), nullable=True, index=True)
    level = db.Column(Integer, default=0, index=True)

    # 自引用关系，用于父子菜单管理
    children = relationship("Sheet", backref=db.backref('parent', remote_side=[id]),
                           cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Sheet {self.kind}:{self.name} (level:{self.level})>"


class SheetColumn(db.Model):
    """工作表列结构 - 驱动左侧多级子菜单 (大类 -> 小项)

    年度账单类: group=收入/支出, name=条目列名(君公司/物业费...),
               item_key=序列化的 (category,owner,sub,name)
    结余类:     group=大类(银行理财/...), name=小项账户名,
               item_key=序列化的 (type,owner,name)
    其他类:     一般不登记 (作为叶子菜单)
    """
    __tablename__ = "sheet_column"
    __table_args__ = (
        UniqueConstraint("sheet_name", "group", "name", name="uq_sheet_col"),
        Index("ix_sheet_col_sheet", "sheet_name"),
    )

    id = db.Column(Integer, primary_key=True)
    sheet_name = db.Column(String(128), nullable=False, index=True)
    group = db.Column(String(32), default="", index=True)   # 大类
    name = db.Column(String(64), nullable=False)           # 小项
    item_key = db.Column(String(160), default="")          # 序列化 key
    sort_order = db.Column(Integer, default=0)

    def __repr__(self):
        return f"<SheetColumn {self.sheet_name}/{self.group}/{self.name}>"


class Item(db.Model):
    """账单条目模板 - 用户可新增 / 排序 / 启用禁用"""
    __tablename__ = "item"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(64), nullable=False)
    # 收入 / 支出 / 结余 / 其他
    category = db.Column(String(16), nullable=False, index=True)
    # 君 / 军 / 家庭 / 公共
    owner = db.Column(String(16), default="家庭")
    # 子分类 (例如 收入-君公司 / 支出-水电)
    sub_category = db.Column(String(32), default="")
    # 来源 Excel 工作表名 (年度账单 sheets), 用于侧边栏归类
    sheet = db.Column(String(64), default="", index=True)
    # 默认值 (用于未填月份提示)
    default_value = db.Column(Numeric(12, 2), default=0)
    unit = db.Column(String(8), default="元")
    sort_order = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, index=True)
    note = db.Column(String(255), default="")
    created_at = db.Column(DateTime, default=datetime.utcnow)

    entries = relationship(
        "Entry", back_populates="item", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Item {self.category}/{self.owner}/{self.name}>"


class Entry(db.Model):
    """月度条目记录 - (year, month, item_id, user_id) 唯一, 用于去重"""
    __tablename__ = "entry"
    __table_args__ = (
        UniqueConstraint("year", "month", "item_id", "user_id", name="uq_entry_month_item_user"),
        Index("ix_entry_period", "year", "month"),
    )

    id = db.Column(Integer, primary_key=True)
    year = db.Column(Integer, nullable=False, index=True)
    month = db.Column(Integer, nullable=False, index=True)  # 1-12
    item_id = db.Column(
        Integer, ForeignKey("item.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(String(64), default="", index=True)
    value = db.Column(Numeric(14, 2), default=0)
    note = db.Column(Text, default="")
    source = db.Column(String(32), default="manual")  # manual / excel / import
    created_at = db.Column(DateTime, default=datetime.utcnow)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    item = relationship("Item", back_populates="entries")

    @property
    def period(self) -> str:
        return f"{self.year}-{self.month:02d}"

    def __repr__(self):
        return f"<Entry {self.period} {self.item.name if self.item else '?'}={self.value}>"


class Account(db.Model):
    """结余账户 (银行理财 / 股市 / 流动资金 / 外债)"""
    __tablename__ = "account"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(64), nullable=False)
    # 银行理财 / 股市理财 / 流动资金 / 信用 / 外债 / 其他
    type = db.Column(String(32), default="银行理财", index=True)
    owner = db.Column(String(16), default="君军之家")  # 李 / 朱 / 君军之家
    # 大类: Excel "家庭结余" 合并单元格文本 (银行理财/股市理财/流动资金（李）/流动资金（朱）/外债)
    group = db.Column(String(32), default="", index=True)
    # 来源 Excel 工作表名 (一般为 "家庭结余")
    sheet = db.Column(String(64), default="", index=True)
    sort_order = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, index=True)
    note = db.Column(String(255), default="")

    snapshots = relationship(
        "BalanceSnapshot", back_populates="account", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Account {self.type}/{self.owner}/{self.name}>"


class BalanceSnapshot(db.Model):
    """月度账户结余快照 - (year, month, account_id, user_id) 唯一"""
    __tablename__ = "balance_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "year", "month", "account_id", "user_id", name="uq_snapshot_month_account_user"
        ),
        Index("ix_snapshot_period", "year", "month"),
    )

    id = db.Column(Integer, primary_key=True)
    year = db.Column(Integer, nullable=False, index=True)
    month = db.Column(Integer, nullable=False, index=True)
    account_id = db.Column(
        Integer, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(String(64), default="", index=True)
    value = db.Column(Numeric(14, 2), default=0)
    note = db.Column(String(255), default="")
    source = db.Column(String(32), default="manual")
    created_at = db.Column(DateTime, default=datetime.utcnow)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="snapshots")

    @property
    def period(self) -> str:
        return f"{self.year}-{self.month:02d}"


class ImportLog(db.Model):
    """数据导入历史 - 记录每次导入的来源 / 命中 / 去重 / 错误"""
    __tablename__ = "import_log"

    id = db.Column(Integer, primary_key=True)
    source_file = db.Column(String(255), nullable=False)
    sheet_name = db.Column(String(128), default="")
    kind = db.Column(String(32), default="entries")  # entries / balances
    year_from = db.Column(Integer)
    year_to = db.Column(Integer)
    rows_imported = db.Column(Integer, default=0)
    rows_skipped = db.Column(Integer, default=0)  # 去重跳过
    rows_error = db.Column(Integer, default=0)
    message = db.Column(Text, default="")
    imported_at = db.Column(DateTime, default=datetime.utcnow)


class EditLock(db.Model):
    """并发编辑锁 - 条目 / 账户级, 默认 3 分钟自动释放

    resource_type:
        'entry'   -> resource_id = Item.id      (在线填写)
        'balance' -> resource_id = Account.id   (月末结余)
    period_key:
        'YYYY-MM' 限定锁的作用域, 不同月份互不影响
    """
    __tablename__ = "edit_lock"
    __table_args__ = (
        UniqueConstraint(
            "resource_type", "resource_id", "period_key",
            name="uq_resource_lock",
        ),
        Index("ix_lock_expires", "expires_at"),
    )

    id = db.Column(Integer, primary_key=True)
    # 'entry' | 'balance'
    resource_type = db.Column(String(16), nullable=False)
    resource_id = db.Column(Integer, nullable=False)
    # 'YYYY-MM' 锁作用域 (只锁当月条目)
    period_key = db.Column(String(8), nullable=False, default="")
    # 持锁用户标识 (session user_id)
    user_id = db.Column(String(64), nullable=False)
    user_label = db.Column(String(64), default="")
    acquired_at = db.Column(DateTime, default=datetime.utcnow)
    expires_at = db.Column(DateTime, nullable=False)

    def __repr__(self):
        return f"<EditLock {self.resource_type}:{self.resource_id} by {self.user_id}>"


class Setting(db.Model):
    """键值配置表 - 持久化全局配置 (锁TTL / 心跳间隔 / 开关 等)"""
    __tablename__ = "setting"

    id = db.Column(Integer, primary_key=True)
    key = db.Column(String(64), nullable=False, unique=True, index=True)
    value = db.Column(Text, default="")
    # 值类型: int / str / bool
    vtype = db.Column(String(8), default="str")
    updated_at = db.Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"
