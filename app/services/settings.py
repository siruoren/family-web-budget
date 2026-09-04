"""设置服务 - 配置管理

提供配置读写功能，从 Setting 表读取/写入配置值。
"""
from flask import g
from sqlalchemy import select

from .. import db
from ..models import Setting


def get_setting(key: str, default=None):
    """从配置表读取单个配置值 (自动按 vtype 转换), 带请求级缓存"""
    cache = getattr(g, "_settings_cache", None)
    if cache is not None and key in cache:
        return cache[key]
    s = db.session.execute(
        select(Setting).where(Setting.key == key)
    ).scalars().first()
    if not s or s.value in (None, ""):
        result = default
    elif s.vtype == "int":
        try:
            result = int(s.value)
        except (ValueError, TypeError):
            result = default
    elif s.vtype == "bool":
        result = s.value.lower() in ("1", "true", "yes", "on")
    else:
        result = s.value
    if cache is not None:
        cache[key] = result
    return result


def set_setting(key: str, value, vtype: str = "str"):
    """写入配置 (upsert), 同时刷新请求级缓存"""
    s = db.session.execute(
        select(Setting).where(Setting.key == key)
    ).scalars().first()
    if s:
        s.value = str(value)
        s.vtype = vtype
    else:
        db.session.add(Setting(
            key=key, value=str(value), vtype=vtype,
        ))
    db.session.commit()
    cache = getattr(g, "_settings_cache", None)
    if cache is not None:
        cache.pop(key, None)
