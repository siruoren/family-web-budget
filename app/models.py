"""
数据模型 (M) - SQLite 持久化

核心实体 (v2 架构重构):
  User         : 用户 (可创建 / 切换 / 设默认), user_id 在 URL 中
  AccountItem  : 账户条目模板 (名称+类型+属主 唯一, 支持批量新增 / 自定义字段)
  Asset        : 月度资产记录 (年+月+条目+用户 唯一, 所有数据来源)
  EditLock     : 并发编辑锁 (3 分钟自动释放)
  Setting      : 键值配置 (锁TTL / 公式 / 全局开关)
"""
from datetime import datetime
from sqlalchemy import (
    Integer, String, Numeric, ForeignKey, DateTime, Boolean, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from . import db


class User(db.Model):
    """用户 - 可在系统配置中创建, 切换, 设默认"""
    __tablename__ = "user"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(32), nullable=False, unique=True)
    is_default = db.Column(Boolean, default=False, index=True)
    sort_order = db.Column(Integer, default=0)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    assets = relationship(
        "Asset", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User {self.name}>"


class AccountItem(db.Model):
    """账户条目模板 - 名称+类型+属主 唯一

    type: 收入 / 支出 / 结余 / 其他 (可自定义)
    owner: 君 / 军 / 家庭 / 公共 (可自定义)
    每个字段都可以在系统配置中调整
    """
    __tablename__ = "account_item"
    __table_args__ = (
        UniqueConstraint("name", "type", "owner", name="uq_item_name_type_owner"),
        Index("ix_item_type", "type"),
    )

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(64), nullable=False)
    type = db.Column(String(32), nullable=False, default="支出")
    owner = db.Column(String(32), nullable=False, default="家庭")
    note = db.Column(String(255), default="")
    sort_order = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, index=True)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    assets = relationship(
        "Asset", back_populates="account_item", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<AccountItem {self.type}/{self.owner}/{self.name}>"


class Asset(db.Model):
    """月度资产记录 - 所有数据的核心来源

    (year, month, account_item_id, user_id) 唯一, 用于去重
    资产计算公式: 上月结余 + 当月收入 - 当月结余 = 支出
    """
    __tablename__ = "asset"
    __table_args__ = (
        UniqueConstraint(
            "year", "month", "account_item_id", "user_id",
            name="uq_asset_period_item_user",
        ),
        Index("ix_asset_period", "year", "month"),
        Index("ix_asset_user", "user_id"),
    )

    id = db.Column(Integer, primary_key=True)
    year = db.Column(Integer, nullable=False, index=True)
    month = db.Column(Integer, nullable=False, index=True)  # 1-12
    account_item_id = db.Column(
        Integer, ForeignKey("account_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    value = db.Column(Numeric(14, 2), default=0)
    note = db.Column(Text, default="")
    source = db.Column(String(32), default="manual")  # manual / import
    created_at = db.Column(DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account_item = relationship("AccountItem", back_populates="assets")
    user = relationship("User", back_populates="assets")

    @property
    def period(self) -> str:
        return f"{self.year}-{self.month:02d}"

    def __repr__(self):
        return f"<Asset {self.period} item={self.account_item_id} val={self.value}>"


class EditLock(db.Model):
    """并发编辑锁 - 条目级, 默认 3 分钟自动释放

    resource_type: 'asset' -> resource_id = AccountItem.id
    period_key: 'YYYY-MM' 限定锁的作用域
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
    resource_type = db.Column(String(16), nullable=False)
    resource_id = db.Column(Integer, nullable=False)
    period_key = db.Column(String(8), nullable=False, default="")
    user_id = db.Column(String(64), nullable=False)
    user_label = db.Column(String(64), default="")
    acquired_at = db.Column(DateTime, default=datetime.utcnow)
    expires_at = db.Column(DateTime, nullable=False)

    def __repr__(self):
        return f"<EditLock {self.resource_type}:{self.resource_id} by {self.user_id}>"


class Setting(db.Model):
    """键值配置表 - 持久化全局配置 (锁TTL / 公式 / 开关)"""
    __tablename__ = "setting"

    id = db.Column(Integer, primary_key=True)
    key = db.Column(String(64), nullable=False, unique=True, index=True)
    value = db.Column(Text, default="")
    vtype = db.Column(String(8), default="str")
    updated_at = db.Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"


class MenuItem(db.Model):
    """左侧菜单项 - 用户可在系统配置中创建多层级菜单

    parent_id 为空表示顶级菜单; 非空表示子菜单 (支持无限层级)
    filter_type / filter_owner 用于筛选点击该菜单时显示的 AccountItem 条目
    若两者均空, 则为纯分组节点 (仅展示子菜单)
    """
    __tablename__ = "menu_item"
    __table_args__ = (
        Index("ix_menu_parent", "parent_id"),
    )

    id = db.Column(Integer, primary_key=True)
    parent_id = db.Column(
        Integer, ForeignKey("menu_item.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    name = db.Column(String(64), nullable=False)
    sort_order = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, index=True)
    filter_type = db.Column(String(32), default="")
    filter_owner = db.Column(String(32), default="")
    icon = db.Column(String(16), default="")
    created_at = db.Column(DateTime, default=datetime.utcnow)

    children = relationship(
        "MenuItem", backref="parent", remote_side="MenuItem.id",
        cascade="all, delete-orphan", order_by="MenuItem.sort_order",
    )

    def __repr__(self):
        return f"<MenuItem {self.name} parent={self.parent_id}>"
