"""数据库结构幂等迁移

SQLAlchemy 的 db.create_all() 只会创建缺失的表, 不会给已有表补列。
本模块在应用启动时检查 item / account 表是否含新增列 (sheet / group),
缺失则用 ALTER TABLE ADD COLUMN 补上, 保证历史 DB 平滑升级到新结构。
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
}


def _existing_columns(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def ensure_schema() -> dict:
    """补齐缺失列, 返回 {table: [新增列名]}"""
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
    return added
