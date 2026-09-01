"""
数据导入服务 - Excel 解析 + 去重 + 持久化

去重策略:
  Entry / BalanceSnapshot 表均有 UNIQUE(year, month, item_id/account_id) 约束。
  导入时:
    1) 解析 Excel 得到 ParsedEntry/ParsedBalance 列表
    2) 按 (year, month, item_key) 分组 (后者保留最新值)
    3) 与 DB 已有记录比对, 已存在则 SKIP (或覆盖 - 取决于 strategy 参数)
    4) 写入 ImportLog
"""
from dataclasses import asdict
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import db
from ..models import Item, Account, Entry, BalanceSnapshot, ImportLog
from .excel_parser import (
    parse_workbook, ParsedEntry, ParsedBalance, ParseResult,
)


# -------------------------------------------------------------- 条目缓存
class _ItemCache:
    """条目名 -> Item 缓存, 缺失时自动创建"""
    def __init__(self):
        self._cache: dict[tuple, Item] = {}
        # 预加载所有条目
        for it in db.session.execute(select(Item)).scalars():
            self._cache[(it.category, it.owner, it.sub_category, it.name)] = it

    def get_or_create(self, key: tuple, sheet: str = "") -> Item:
        """key = (category, owner, sub_category, name)"""
        if key in self._cache:
            it = self._cache[key]
            if sheet and not it.sheet:
                it.sheet = sheet
                db.session.flush()
            return it
        cat, owner, sub, name = key
        # 同名 + 同 category 视为相同
        existing = db.session.execute(
            select(Item).where(
                Item.category == cat, Item.name == name,
                Item.owner == owner, Item.sub_category == sub,
            )
        ).scalars().first()
        if existing:
            if sheet and not existing.sheet:
                existing.sheet = sheet
                db.session.flush()
            self._cache[key] = existing
            return existing
        item = Item(
            category=cat, owner=owner, sub_category=sub, name=name,
            sort_order=999, sheet=sheet,
        )
        db.session.add(item)
        db.session.flush()  # 拿到 id
        self._cache[key] = item
        return item


class _AccountCache:
    """账户名 -> Account 缓存, 缺失时自动创建"""
    def __init__(self):
        self._cache: dict[tuple, Account] = {}

    def get_or_create(self, key: tuple, group: str = "",
                      sheet: str = "") -> Account:
        if key in self._cache:
            return self._cache[key]
        typ, owner, name = key
        existing = db.session.execute(
            select(Account).where(
                Account.type == typ, Account.owner == owner,
                Account.name == name,
            )
        ).scalars().first()
        if existing:
            # 回填结构信息 (首次导入旧库可能缺失)
            changed = False
            if group and not existing.group:
                existing.group = group; changed = True
            if sheet and not existing.sheet:
                existing.sheet = sheet; changed = True
            if changed:
                db.session.flush()
            self._cache[key] = existing
            return existing
        acc = Account(type=typ, owner=owner, name=name, sort_order=999,
                      group=group, sheet=sheet)
        db.session.add(acc)
        db.session.flush()
        self._cache[key] = acc
        return acc


# -------------------------------------------------------------- 去重分组
def _group_entries(entries: list[ParsedEntry]) -> dict[tuple, ParsedEntry]:
    """(year, month, item_key) -> 最后一条 (覆盖式合并)"""
    grouped: dict[tuple, ParsedEntry] = {}
    for e in entries:
        grouped[(e.year, e.month, e.item_key)] = e
    return grouped


def _group_balances(balances: list[ParsedBalance]) -> dict[tuple, ParsedBalance]:
    grouped: dict[tuple, ParsedBalance] = {}
    for b in balances:
        grouped[(b.year, b.month, b.account_key)] = b
    return grouped


# -------------------------------------------------------------- SQLite 批量 upsert
def _upsert_entries(grouped: dict[tuple, ParsedEntry], item_cache: _ItemCache,
                    strategy: str = "skip", user_id: str = "") -> tuple[int, int, int]:
    """返回 (imported, skipped, error)"""
    imported = skipped = error = 0
    for (year, month, item_key), pe in grouped.items():
        try:
            item = item_cache.get_or_create(item_key, sheet=pe.sheet)
            existing = db.session.execute(
                select(Entry).where(
                    Entry.year == year, Entry.month == month,
                    Entry.item_id == item.id,
                    Entry.user_id == user_id,
                )
            ).scalars().first()

            if existing:
                if strategy == "overwrite":
                    existing.value = pe.value
                    existing.note = pe.note or existing.note
                    existing.source = "excel"
                    imported += 1
                else:  # skip
                    skipped += 1
            else:
                db.session.add(Entry(
                    year=year, month=month, item_id=item.id,
                    value=pe.value, note=pe.note, source="excel",
                    user_id=user_id,
                ))
                imported += 1
        except Exception:  # noqa: BLE001
            error += 1
            db.session.rollback()
            item_cache = _ItemCache()  # 重置缓存
    return imported, skipped, error


def _upsert_balances(grouped: dict[tuple, ParsedBalance],
                     acc_cache: _AccountCache,
                     strategy: str = "skip", user_id: str = "") -> tuple[int, int, int]:
    imported = skipped = error = 0
    for (year, month, acc_key), pb in grouped.items():
        try:
            acc = acc_cache.get_or_create(
                acc_key, group=pb.group, sheet=pb.sheet
            )
            existing = db.session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.year == year, BalanceSnapshot.month == month,
                    BalanceSnapshot.account_id == acc.id,
                    BalanceSnapshot.user_id == user_id,
                )
            ).scalars().first()
            if existing:
                if strategy == "overwrite":
                    existing.value = pb.value
                    existing.note = pb.note or existing.note
                    existing.source = "excel"
                    imported += 1
                else:
                    skipped += 1
            else:
                db.session.add(BalanceSnapshot(
                    year=year, month=month, account_id=acc.id,
                    value=pb.value, note=pb.note, source="excel",
                    user_id=user_id,
                ))
                imported += 1
        except Exception:  # noqa: BLE001
            error += 1
            db.session.rollback()
            acc_cache = _AccountCache()
    return imported, skipped, error


# -------------------------------------------------------------- 主入口
def import_excel(path: str, strategy: str = "skip", user_id: str = "") -> dict:
    """导入 Excel 文件 -> 返回汇总统计 (按 user_id 隔离)"""
    results = parse_workbook(path)
    summary = {
        "total_imported": 0, "total_skipped": 0, "total_error": 0,
        "sheets": [], "year_from": None, "year_to": None,
    }
    item_cache = _ItemCache()
    acc_cache = _AccountCache()
    years_seen = set()

    for r in results:
        if r.kind == "entries" and r.entries:
            grouped = _group_entries(r.entries)
            imp, skip, err = _upsert_entries(grouped, item_cache, strategy, user_id=user_id)
        elif r.kind == "balances" and r.balances:
            grouped = _group_balances(r.balances)
            imp, skip, err = _upsert_balances(grouped, acc_cache, strategy, user_id=user_id)
        else:
            imp, skip, err = 0, 0, 0

        if r.year_from:
            years_seen.add(r.year_from)
        if r.year_to:
            years_seen.add(r.year_to)

        summary["total_imported"] += imp
        summary["total_skipped"] += skip
        summary["total_error"] += err
        summary["sheets"].append({
            "name": r.sheet_name, "kind": r.kind,
            "scanned": r.rows_scanned, "imported": imp,
            "skipped": skip, "error": err,
        })

    db.session.commit()

    if years_seen:
        summary["year_from"] = min(years_seen)
        summary["year_to"] = max(years_seen)

    # 写入 ImportLog
    log = ImportLog(
        source_file=path.split("/")[-1],
        kind="excel",
        year_from=summary["year_from"],
        year_to=summary["year_to"],
        rows_imported=summary["total_imported"],
        rows_skipped=summary["total_skipped"],
        rows_error=summary["total_error"],
        message=strategy,
    )
    db.session.add(log)
    db.session.commit()

    return summary
