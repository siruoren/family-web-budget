"""数据库结构幂等迁移

SQLAlchemy 的 db.create_all() 只会创建缺失的表, 不会给已有表补列。
本模块在应用启动时检查 item / account / entry / balance_snapshot 表
是否含新增列, 缺失则用 ALTER TABLE ADD COLUMN 补上,
并重建唯一索引以包含 user_id, 保证历史 DB 平滑升级到新结构。
"""
from sqlalchemy import inspect, text

from .. import db


# 表 -> 期望列清单 (列名, 列定义 SQL 片段, 用于 ALTER TABLE)
_EXPECTED_COLUMNS = {
    "item": [
        ("sheet", "VARCHAR(64) DEFAULT ''"),
    ],
    "account": [
        ("sheet", "VARCHAR(64) DEFAULT ''"),
        ("group", "VARCHAR(32) DEFAULT ''"),
    ],
    "entry": [
        ("user_id", "VARCHAR(64) DEFAULT ''"),
    ],
    "balance_snapshot": [
        ("user_id", "VARCHAR(64) DEFAULT ''"),
    ],
}

# 旧唯一索引 -> 新唯一索引 (包含 user_id)
_INDEX_REBUILDS = [
    {
        "table": "entry",
        "old_idx": "uq_entry_month_item",
        "new_idx": "uq_entry_month_item_user",
        "cols": "year, month, item_id, user_id",
    },
    {
        "table": "balance_snapshot",
        "old_idx": "uq_snapshot_month_account",
        "new_idx": "uq_snapshot_month_account_user",
        "cols": "year, month, account_id, user_id",
    },
]


def _existing_columns(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _rebuild_unique_indexes() -> list[str]:
    """重建唯一索引以包含 user_id, 返回重建的索引名列表"""
    rebuilt: list[str] = []
    for spec in _INDEX_REBUILDS:
        table = spec["table"]
        try:
            db.session.execute(text(f'DROP INDEX IF EXISTS "{spec["old_idx"]}"'))
            db.session.execute(text(
                f'DROP INDEX IF EXISTS "{spec["new_idx"]}"'
            ))
            db.session.execute(text(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "{spec["new_idx"]}" '
                f'ON "{table}" ({spec["cols"]})'
            ))
            rebuilt.append(spec["new_idx"])
        except Exception:
            db.session.rollback()
    db.session.commit()
    return rebuilt


def ensure_schema() -> dict:
    """补齐缺失列 + 重建唯一索引, 返回 {table: [新增列名]}"""
    added: dict[str, list[str]] = {}
    inspector = inspect(db.engine)
    for table, cols in _EXPECTED_COLUMNS.items():
        have = _existing_columns(inspector, table)
        missing = [c for c in cols if c[0] not in have]
        if not missing:
            continue
        for col_name, col_def in missing:
            db.session.execute(
                text(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {col_def}')
            )
            added.setdefault(table, []).append(col_name)
        db.session.commit()

    # 列补齐后重建唯一索引 (含 user_id)
    fresh = inspect(db.engine)
    entry_cols = _existing_columns(fresh, "entry")
    snap_cols = _existing_columns(fresh, "balance_snapshot")
    if "user_id" in entry_cols or "user_id" in snap_cols:
        _rebuild_unique_indexes()

    return added
