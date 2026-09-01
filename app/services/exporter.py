"""
数据导出服务

- 指定月份/年份的 SQLite 子集导出 (复制为 .db)
- Excel 导出 (按月份 / 年份)
- JSON 导出
- 整库迁移导出 (db 文件)
"""
from __future__ import annotations
import os
import json
import shutil
import sqlite3
import tempfile
from io import BytesIO
from sqlalchemy import select, and_
from datetime import datetime

from ..models import (
    Item, Entry, Account, BalanceSnapshot, ImportLog,
)
from .. import db


# -------------------------------------------------------------- JSON 导出
def export_json(year: int | None = None, month: int | None = None) -> bytes:
    """导出指定月份 / 年份的条目 + 结余为 JSON"""
    entry_q = select(Entry, Item).join(Item, Entry.item_id == Item.id)
    if year:
        entry_q = entry_q.where(Entry.year == year)
    if month:
        entry_q = entry_q.where(Entry.month == month)
    entry_q = entry_q.order_by(Entry.year, Entry.month, Item.category, Item.sort_order)

    entries = []
    for e, it in db.session.execute(entry_q).all():
        entries.append({
            "year": e.year, "month": e.month, "category": it.category,
            "owner": it.owner, "sub_category": it.sub_category,
            "item": it.name, "value": float(e.value or 0),
            "note": e.note or "", "source": e.source,
        })

    bal_q = select(BalanceSnapshot, Account).join(
        Account, BalanceSnapshot.account_id == Account.id
    )
    if year:
        bal_q = bal_q.where(BalanceSnapshot.year == year)
    if month:
        bal_q = bal_q.where(BalanceSnapshot.month == month)
    bal_q = bal_q.order_by(
        BalanceSnapshot.year, BalanceSnapshot.month, Account.sort_order
    )
    balances = []
    for b, a in db.session.execute(bal_q).all():
        balances.append({
            "year": b.year, "month": b.month, "type": a.type,
            "owner": a.owner, "account": a.name,
            "value": float(b.value or 0), "note": b.note or "",
        })

    payload = {
        "exported_at": datetime.utcnow().isoformat(),
        "filter": {"year": year, "month": month},
        "entries": entries,
        "balances": balances,
        "entries_count": len(entries),
        "balances_count": len(balances),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


# -------------------------------------------------------------- Excel 导出
def export_excel(year: int | None = None, month: int | None = None) -> BytesIO:
    """导出为 .xlsx (条目表 + 结余表)"""
    import openpyxl
    from openpyxl.styles import Font, Alignment

    wb = openpyxl.Workbook()
    # 条目表
    ws1 = wb.active
    ws1.title = "账单条目"
    headers = ["年份", "月份", "分类", "归属", "子分类", "条目", "金额", "备注", "来源"]
    ws1.append(headers)
    for cell in ws1[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    entry_q = select(Entry, Item).join(Item, Entry.item_id == Item.id)
    if year:
        entry_q = entry_q.where(Entry.year == year)
    if month:
        entry_q = entry_q.where(Entry.month == month)
    entry_q = entry_q.order_by(Entry.year, Entry.month, Item.category, Item.sort_order)
    for e, it in db.session.execute(entry_q).all():
        ws1.append([
            e.year, e.month, it.category, it.owner, it.sub_category,
            it.name, float(e.value or 0), e.note or "", e.source or "",
        ])

    # 结余表
    ws2 = wb.create_sheet("月末结余")
    bal_headers = ["年份", "月份", "类型", "归属", "账户", "金额", "备注"]
    ws2.append(bal_headers)
    for cell in ws2[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    bal_q = select(BalanceSnapshot, Account).join(
        Account, BalanceSnapshot.account_id == Account.id
    )
    if year:
        bal_q = bal_q.where(BalanceSnapshot.year == year)
    if month:
        bal_q = bal_q.where(BalanceSnapshot.month == month)
    bal_q = bal_q.order_by(
        BalanceSnapshot.year, BalanceSnapshot.month, Account.sort_order
    )
    for b, a in db.session.execute(bal_q).all():
        ws2.append([
            b.year, b.month, a.type, a.owner, a.name,
            float(b.value or 0), b.note or "",
        ])

    # 自动列宽
    for ws in (ws1, ws2):
        for col in ws.columns:
            max_len = max(len(str(c.value)) if c.value is not None else 0
                         for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# -------------------------------------------------------------- SQLite 导出
def export_sqlite(target_path: str, year: int | None = None,
                  month: int | None = None) -> str:
    """
    导出 SQLite 数据库文件
    - year/month 都为 None: 整库迁移 (复制当前 db)
    - 否则: 新建 db, 仅复制匹配的 Entry / BalanceSnapshot + 相关 Item/Account
    """
    from flask import current_app
    src_db = current_app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")

    if year is None and month is None:
        # 整库迁移 - 直接复制
        shutil.copy2(src_db, target_path)
        return target_path

    # 子集导出 - 新建空库 + 复制结构 + 写入匹配数据
    if os.path.exists(target_path):
        os.remove(target_path)
    # 复制整库作为基础 (结构 + 静态表: Item / Account)
    shutil.copy2(src_db, target_path)

    conn = sqlite3.connect(target_path)
    cur = conn.cursor()
    # 清空动态数据
    for tbl in ("entry", "balance_snapshot", "import_log"):
        cur.execute(f"DELETE FROM {tbl};")

    # 重新写入匹配年份/月份的数据
    entry_q = select(Entry).where(
        and_(
            Entry.year == year if year else Entry.year,
            Entry.month == month if month else Entry.month,
        )
    )
    for e in db.session.execute(entry_q).scalars():
        cur.execute(
            "INSERT INTO entry (year, month, item_id, value, note, source, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (e.year, e.month, e.item_id, float(e.value or 0), e.note or "",
             e.source, e.created_at, e.updated_at),
        )

    bal_q = select(BalanceSnapshot).where(
        and_(
            BalanceSnapshot.year == year if year else BalanceSnapshot.year,
            BalanceSnapshot.month == month if month else BalanceSnapshot.month,
        )
    )
    for b in db.session.execute(bal_q).scalars():
        cur.execute(
            "INSERT INTO balance_snapshot (year, month, account_id, value, "
            "note, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (b.year, b.month, b.account_id, float(b.value or 0), b.note or "",
             b.source, b.created_at, b.updated_at),
        )
    conn.commit()
    conn.close()
    return target_path


# -------------------------------------------------------------- 整库导入 (迁移)
def import_sqlite_db(src_path: str, strategy: str = "skip") -> dict:
    """从外部 SQLite db 文件导入数据 (整库迁移导入)"""
    conn = sqlite3.connect(src_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    summary = {"entries": 0, "balances": 0, "skipped": 0, "errors": []}

    # 条目
    try:
        rows = cur.execute("SELECT * FROM entry").fetchall()
    except sqlite3.OperationalError:
        rows = []

    for r in rows:
        try:
            existing = db.session.execute(
                select(Entry).where(
                    Entry.year == r["year"], Entry.month == r["month"],
                    Entry.item_id == r["item_id"],
                )
            ).scalars().first()
            if existing:
                if strategy == "overwrite":
                    existing.value = r["value"]
                    existing.note = r["note"] or existing.note
                    summary["entries"] += 1
                else:
                    summary["skipped"] += 1
            else:
                db.session.add(Entry(
                    year=r["year"], month=r["month"], item_id=r["item_id"],
                    value=float(r["value"] or 0), note=r["note"] or "",
                    source=r.get("source") or "import",
                ))
                summary["entries"] += 1
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(str(e))

    # 结余
    try:
        b_rows = cur.execute("SELECT * FROM balance_snapshot").fetchall()
    except sqlite3.OperationalError:
        b_rows = []
    for r in b_rows:
        try:
            existing = db.session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.year == r["year"],
                    BalanceSnapshot.month == r["month"],
                    BalanceSnapshot.account_id == r["account_id"],
                )
            ).scalars().first()
            if existing:
                if strategy == "overwrite":
                    existing.value = r["value"]
                    summary["balances"] += 1
                else:
                    summary["skipped"] += 1
            else:
                db.session.add(BalanceSnapshot(
                    year=r["year"], month=r["month"],
                    account_id=r["account_id"],
                    value=float(r["value"] or 0),
                    note=r["note"] or "", source=r.get("source") or "import",
                ))
                summary["balances"] += 1
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(str(e))

    db.session.commit()
    conn.close()

    # 日志
    log = ImportLog(
        source_file=os.path.basename(src_path), kind="sqlite",
        rows_imported=summary["entries"] + summary["balances"],
        rows_skipped=summary["skipped"],
        rows_error=len(summary["errors"]),
        message=strategy,
    )
    db.session.add(log)
    db.session.commit()
    return summary


# -------------------------------------------------------------- 整库 JSON 导入
def import_json_file(path: str, strategy: str = "skip") -> dict:
    """从 JSON 文件导入 (兼容 export_json 的格式)"""
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    summary = {"entries": 0, "balances": 0, "skipped": 0, "errors": []}

    # 预加载条目/账户缓存
    items_by_key: dict[tuple, Item] = {}
    for it in db.session.execute(select(Item)).scalars():
        items_by_key[(it.category, it.owner, it.sub_category, it.name)] = it
    accs_by_key: dict[tuple, Account] = {}
    for a in db.session.execute(select(Account)).scalars():
        accs_by_key[(a.type, a.owner, a.name)] = a

    for e in payload.get("entries", []):
        key = (e["category"], e["owner"], e.get("sub_category", ""), e["item"])
        item = items_by_key.get(key)
        if not item:
            item = Item(
                category=e["category"], owner=e["owner"],
                sub_category=e.get("sub_category", ""), name=e["item"],
                sort_order=999,
            )
            db.session.add(item)
            db.session.flush()
            items_by_key[key] = item
        try:
            existing = db.session.execute(
                select(Entry).where(
                    Entry.year == e["year"], Entry.month == e["month"],
                    Entry.item_id == item.id,
                )
            ).scalars().first()
            if existing:
                if strategy == "overwrite":
                    existing.value = e["value"]
                    existing.note = e.get("note", "")
                    summary["entries"] += 1
                else:
                    summary["skipped"] += 1
            else:
                db.session.add(Entry(
                    year=e["year"], month=e["month"], item_id=item.id,
                    value=e["value"], note=e.get("note", ""),
                    source=e.get("source", "import"),
                ))
                summary["entries"] += 1
        except Exception as ex:  # noqa: BLE001
            summary["errors"].append(str(ex))

    for b in payload.get("balances", []):
        key = (b["type"], b["owner"], b["account"])
        acc = accs_by_key.get(key)
        if not acc:
            acc = Account(type=b["type"], owner=b["owner"], name=b["account"],
                          sort_order=999)
            db.session.add(acc)
            db.session.flush()
            accs_by_key[key] = acc
        try:
            existing = db.session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.year == b["year"],
                    BalanceSnapshot.month == b["month"],
                    BalanceSnapshot.account_id == acc.id,
                )
            ).scalars().first()
            if existing:
                if strategy == "overwrite":
                    existing.value = b["value"]
                    summary["balances"] += 1
                else:
                    summary["skipped"] += 1
            else:
                db.session.add(BalanceSnapshot(
                    year=b["year"], month=b["month"], account_id=acc.id,
                    value=b["value"], note=b.get("note", ""),
                    source=b.get("source", "import"),
                ))
                summary["balances"] += 1
        except Exception as ex:  # noqa: BLE001
            summary["errors"].append(str(ex))

    db.session.commit()
    log = ImportLog(
        source_file=os.path.basename(path), kind="json",
        rows_imported=summary["entries"] + summary["balances"],
        rows_skipped=summary["skipped"],
        rows_error=len(summary["errors"]),
        message=strategy,
    )
    db.session.add(log)
    db.session.commit()
    return summary
