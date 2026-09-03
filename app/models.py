"""
数据模型 (M) - SQLite 持久化

核心实体 (v2 架构重构 + 安全增强):
  User         : 用户 (可创建/切换/设默认), 含 password_hash / is_admin
  AccountItem  : 账户条目模板 (名称+类型+属主 唯一)
  Asset        : 月度资产记录; value/note 应用层混淆加密存储 (防 DB 文件泄露)
  EditLock     : 并发编辑锁 (3 分钟自动释放)
  Setting      : 键值配置 (锁TTL / 公式 / 全局开关)
  MenuItem     : 左侧多级菜单

安全说明:
  - Asset.value / Asset.note 以密文形式存于原列 (enc:v1: 前缀区分遗留明文)
  - 模型层 hybrid_property 透明加解密: 读=解密(需当前用户密钥), 写=加密
  - 旧数据 (明文数字/文本) 通过前缀检测向后兼容, 无需迁移即可读
"""
from datetime import datetime
from sqlalchemy import (
    Integer, String, Numeric, ForeignKey, DateTime, Boolean, Text,
    UniqueConstraint, Index, inspect as sa_inspect, text,
)
from sqlalchemy.orm import relationship, backref
from sqlalchemy.ext.hybrid import hybrid_property

from . import db
from .services import crypto


# -------------------------------------------------------------- schema 迁移
def ensure_schema():
    """轻量迁移: 为已存在的表补齐新增列 (SQLite 不自动加列)

    在 create_app 建表之后、种子之前调用, 幂等。
    """
    from . import db
    engine = db.engine
    insp = sa_inspect(engine)

    def _add_col(table: str, column: str, decl: str):
        if table not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns(table)}
        if column in cols:
            return
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))
        except Exception:
            # 列已存在或并发, 忽略
            pass

    _add_col("user", "password_hash", "VARCHAR(255) DEFAULT ''")
    _add_col("user", "is_admin", "BOOLEAN DEFAULT 0")
    _add_col("menu_item", "item_ids", "TEXT DEFAULT ''")


class User(db.Model):
    """用户 - 可在系统配置中创建/切换/设默认

    password_hash: 用户密码哈希 (pbkdf2$iters$salt$hash), 空串=未设密码(不门禁)
    is_admin: 是否管理员 (预留, 管理员门禁以 config.yml 配置为准)
    """
    __tablename__ = "user"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(32), nullable=False, unique=True)
    is_default = db.Column(Boolean, default=False, index=True)
    sort_order = db.Column(Integer, default=0)
    created_at = db.Column(DateTime, default=datetime.utcnow)
    password_hash = db.Column(String(255), default="")
    is_admin = db.Column(Boolean, default=False, index=True)

    assets = relationship(
        "Asset", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    def __repr__(self):
        return f"<User {self.name}>"


class AccountItem(db.Model):
    """账户条目模板 - 名称+类型+属主 唯一 (配置/元数据, 不加密)

    type 字段值应对应 ItemType.name; 通过独立的 ItemType 表管理可创建的类型。
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


class ItemType(db.Model):
    """账户条目类型 - 独立管理, 用户可手动创建新类型 (收入/支出/结余/投资/储蓄...)

    AccountItem.type 字段值对应此表 name; 通过系统配置 → 类型管理 增删改。
    删除时若仍有 AccountItem 引用该类型则阻止 (需先迁移条目)。
    """
    __tablename__ = "item_type"
    __table_args__ = (
        UniqueConstraint("name", name="uq_item_type_name"),
        Index("ix_item_type_sort", "sort_order"),
    )

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(32), nullable=False, unique=True)
    sort_order = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, index=True)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ItemType {self.name}>"


class Asset(db.Model):
    """月度资产记录 - 所有数据的核心来源

    (year, month, account_item_id, user_id) 唯一, 用于去重

    安全: value/note 以密文存储于同名列 (enc:v1: 前缀); 通过 hybrid_property
    透明加解密。旧明文数据 (无前缀) 自动兼容读取。
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
    # value/note 列复用为密文存储; 模型属性改名为 value_enc/note_enc
    value_enc = db.Column("value", Text, default="")
    note_enc = db.Column("note", Text, default="")
    source = db.Column(String(32), default="manual")  # manual / import
    created_at = db.Column(DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account_item = relationship("AccountItem", back_populates="assets")
    user = relationship("User", back_populates="assets")

    # ---- 透明加解密 hybrid 属性 ----
    @hybrid_property
    def value(self) -> float:
        key = crypto.get_current_user_key()
        if key is None:
            # 未解锁: 密文返回 0, 遗留明文返回原值 (不暴露密文)
            raw = self.value_enc
            if not raw:
                return 0.0
            if not crypto.is_encrypted(raw):
                try:
                    return float(raw)
                except Exception:
                    return 0.0
            return 0.0
        return crypto.decrypt_float(self.value_enc, key)

    @value.setter
    def value(self, v):
        key = crypto.get_current_user_key()
        if key is None:
            # 未解锁不应写敏感数据; 回退为明文 (兼容旧路径, 但会告警)
            self.value_enc = repr(float(v or 0))
        else:
            self.value_enc = crypto.encrypt_float(v or 0, key)

    @hybrid_property
    def note(self) -> str:
        key = crypto.get_current_user_key()
        if key is None:
            raw = self.note_enc
            if not raw:
                return ""
            return raw if not crypto.is_encrypted(raw) else ""
        return crypto.decrypt_str(self.note_enc, key)

    @note.setter
    def note(self, v):
        key = crypto.get_current_user_key()
        if key is None:
            self.note_enc = v or ""
        else:
            self.note_enc = crypto.encrypt_str(v or "", key)

    @property
    def period(self) -> str:
        return f"{self.year}-{self.month:02d}"

    def __repr__(self):
        return f"<Asset {self.period} item={self.account_item_id}>"


class EditLock(db.Model):
    """并发编辑锁 - 条目级, 默认 3 分钟自动释放"""
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

    item_ids: 逗号分隔的 AccountItem ID 列表; 非空时点击菜单显示自定义组合视图。
    (filter_type / filter_owner 仍保留兼容旧菜单, 但新菜单推荐用 item_ids)
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
    item_ids = db.Column(Text, default="")
    icon = db.Column(String(16), default="")
    created_at = db.Column(DateTime, default=datetime.utcnow)

    children = relationship(
        "MenuItem", backref=backref("parent", remote_side=[id]),
        cascade="all, delete-orphan", order_by="MenuItem.sort_order",
    )

    def parsed_item_ids(self) -> list[int]:
        """将 item_ids 字段解析为 int 列表"""
        if not self.item_ids:
            return []
        out = []
        for part in self.item_ids.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out

    def __repr__(self):
        return f"<MenuItem {self.name} parent={self.parent_id}>"
