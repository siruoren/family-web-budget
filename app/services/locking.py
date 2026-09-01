"""并发编辑锁服务 - 条目 / 账户级, 默认 3 分钟自动释放

核心 API:
  acquire_lock(...)  : 尝试获取锁, 成功返回 True; 已被他人持有时返回 False
  release_lock(...)  : 释放锁 (仅持锁者可释放)
  heartbeat_lock(...) : 延长锁的有效期 (用户仍在编辑)
  list_locks(...)    : 查询指定周期内的活跃锁 (供模板批量渲染)
  list_all_locks()   : 查询所有活跃锁 (供后台管理页面)
  force_release(...) : 强制释放锁 (后台管理员, 无视持锁者)
  cleanup_expired() : 清理所有过期锁 (由 acquire / heartbeat 触发)
  get_lock_ttl()    : 从配置表读取锁 TTL (默认 180s)
  is_lock_enabled() : 从配置表读取锁开关 (默认开启)

锁作用域: (resource_type, resource_id, period_key)
  - entry   / Item.id   / 'YYYY-MM'
  - balance / Account.id / 'YYYY-MM'
"""
from datetime import datetime, timedelta
from typing import Optional

from flask import g
from sqlalchemy import select, delete, func

from .. import db
from ..models import EditLock, Setting

# 默认锁有效期 (秒) - 3 分钟无操作自动释放
DEFAULT_TTL = 180


# -------------------------------------------------------------- 配置读取
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


def get_lock_ttl() -> int:
    """获取锁 TTL (秒), 默认 180"""
    return get_setting("lock_ttl", DEFAULT_TTL)


def is_lock_enabled() -> bool:
    """并发锁是否启用 (默认启用)"""
    return get_setting("lock_enabled", True)


def _period_key(year: int, month: int) -> str:
    """生成锁作用域 key: 'YYYY-MM'"""
    return f"{int(year):04d}-{int(month):02d}"


def cleanup_expired() -> int:
    """清理所有过期锁, 返回清理条数"""
    now = datetime.utcnow()
    result = db.session.execute(
        delete(EditLock).where(EditLock.expires_at < now)
    )
    db.session.commit()
    return result.rowcount


def acquire_lock(
    resource_type: str,
    resource_id: int,
    user_id: str,
    year: int,
    month: int,
    user_label: str = "",
    ttl: Optional[int] = None,
) -> tuple[bool, Optional[EditLock]]:
    """尝试获取锁

    返回 (success, lock_or_holder):
        - 成功: (True, 自己的 lock)
        - 失败: (False, 持有者的 lock)  # 用于提示"被谁锁定"
    """
    # 锁功能被禁用 -> 直接放行 (视为获取成功但不持久化)
    if not is_lock_enabled():
        return True, None
    if ttl is None:
        ttl = get_lock_ttl()
    cleanup_expired()

    pk = _period_key(year, month)
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl)

    existing = db.session.execute(
        select(EditLock).where(
            EditLock.resource_type == resource_type,
            EditLock.resource_id == resource_id,
            EditLock.period_key == pk,
        )
    ).scalars().first()

    if existing:
        # 自己持锁 -> 续期 (幂等)
        if existing.user_id == user_id:
            existing.expires_at = expires
            existing.acquired_at = now
            if user_label:
                existing.user_label = user_label
            db.session.commit()
            return True, existing
        # 他人持锁 -> 冲突
        return False, existing

    lock = EditLock(
        resource_type=resource_type,
        resource_id=resource_id,
        period_key=pk,
        user_id=user_id,
        user_label=user_label or user_id,
        acquired_at=now,
        expires_at=expires,
    )
    db.session.add(lock)
    try:
        db.session.commit()
        return True, lock
    except Exception:
        db.session.rollback()
        # 并发竞争: 重新查询持有者
        existing = db.session.execute(
            select(EditLock).where(
                EditLock.resource_type == resource_type,
                EditLock.resource_id == resource_id,
                EditLock.period_key == pk,
            )
        ).scalars().first()
        return False, existing


def release_lock(
    resource_type: str,
    resource_id: int,
    user_id: str,
    year: int,
    month: int,
) -> bool:
    """释放锁 (仅持锁者可释放), 返回是否释放成功"""
    pk = _period_key(year, month)
    existing = db.session.execute(
        select(EditLock).where(
            EditLock.resource_type == resource_type,
            EditLock.resource_id == resource_id,
            EditLock.period_key == pk,
        )
    ).scalars().first()

    if not existing:
        return True  # 已无锁, 视为释放成功
    if existing.user_id != user_id:
        return False  # 非持锁者, 拒绝
    db.session.delete(existing)
    db.session.commit()
    return True


def heartbeat_lock(
    resource_type: str,
    resource_id: int,
    user_id: str,
    year: int,
    month: int,
    ttl: Optional[int] = None,
) -> tuple[bool, Optional[EditLock]]:
    """心跳续期 - 用户仍在编辑, 延长有效期

    返回 (success, lock):
        - 自己持锁且续期成功: (True, lock)
        - 锁被他人持有/已过期: (False, holder_or_None)
    """
    if not is_lock_enabled():
        return True, None
    if ttl is None:
        ttl = get_lock_ttl()
    cleanup_expired()
    pk = _period_key(year, month)
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl)

    existing = db.session.execute(
        select(EditLock).where(
            EditLock.resource_type == resource_type,
            EditLock.resource_id == resource_id,
            EditLock.period_key == pk,
        )
    ).scalars().first()

    if not existing:
        # 锁已过期, 尝试重新获取
        return acquire_lock(
            resource_type, resource_id, user_id, year, month, ttl=ttl
        )
    if existing.user_id != user_id:
        return False, existing
    existing.expires_at = expires
    db.session.commit()
    return True, existing


def list_locks(
    resource_type: str,
    year: int,
    month: int,
) -> dict[int, dict]:
    """查询指定周期内某类资源的活跃锁, 返回 {resource_id: lock_info}

    lock_info: {user_id, user_label, expires_at, remaining_seconds}
    """
    cleanup_expired()
    pk = _period_key(year, month)
    now = datetime.utcnow()
    locks = db.session.execute(
        select(EditLock).where(
            EditLock.resource_type == resource_type,
            EditLock.period_key == pk,
        )
    ).scalars().all()

    result = {}
    for lk in locks:
        remaining = int((lk.expires_at - now).total_seconds())
        if remaining <= 0:
            continue
        result[lk.resource_id] = {
            "user_id": lk.user_id,
            "user_label": lk.user_label or lk.user_id,
            "expires_at": lk.expires_at.isoformat(),
            "remaining_seconds": remaining,
        }
    return result


def check_conflict(
    resource_type: str,
    resource_id: int,
    user_id: str,
    year: int,
    month: int,
) -> Optional[dict]:
    """检查指定资源是否被他人锁定 (用于提交时冲突检测)

    返回 None = 无冲突; 否则返回持有者信息
    """
    cleanup_expired()
    pk = _period_key(year, month)
    now = datetime.utcnow()
    existing = db.session.execute(
        select(EditLock).where(
            EditLock.resource_type == resource_type,
            EditLock.resource_id == resource_id,
            EditLock.period_key == pk,
        )
    ).scalars().first()

    if not existing:
        return None
    if existing.user_id == user_id:
        return None  # 自己持锁, 无冲突
    remaining = int((existing.expires_at - now).total_seconds())
    if remaining <= 0:
        # 已过期, 清理
        db.session.delete(existing)
        db.session.commit()
        return None
    return {
        "user_id": existing.user_id,
        "user_label": existing.user_label or existing.user_id,
        "remaining_seconds": remaining,
    }


def list_all_locks() -> list[dict]:
    """查询所有活跃锁 (供后台管理页面), 返回列表

    每项: {id, resource_type, resource_id, period_key, user_id,
           user_label, acquired_at, expires_at, remaining_seconds}
    """
    cleanup_expired()
    now = datetime.utcnow()
    locks = db.session.execute(
        select(EditLock).order_by(EditLock.expires_at.desc())
    ).scalars().all()
    result = []
    for lk in locks:
        remaining = int((lk.expires_at - now).total_seconds())
        if remaining <= 0:
            continue
        result.append({
            "id": lk.id,
            "resource_type": lk.resource_type,
            "resource_id": lk.resource_id,
            "period_key": lk.period_key,
            "user_id": lk.user_id,
            "user_label": lk.user_label or lk.user_id,
            "acquired_at": lk.acquired_at,
            "expires_at": lk.expires_at,
            "remaining_seconds": remaining,
        })
    return result


def force_release(lock_id: int) -> bool:
    """强制释放锁 (后台管理员, 无视持锁者), 返回是否删除成功"""
    lk = db.session.execute(
        select(EditLock).where(EditLock.id == lock_id)
    ).scalars().first()
    if not lk:
        return False
    db.session.delete(lk)
    db.session.commit()
    return True


def force_release_all() -> int:
    """强制释放所有锁 (一键清空), 返回删除条数"""
    result = db.session.execute(delete(EditLock))
    db.session.commit()
    return result.rowcount
