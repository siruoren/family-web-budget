"""工作表视图 - 左侧大菜单点击后的 sheet 详情页

  /sheet/<name>  : 展示该工作表的结构与数据
    - balances 类: 按大类分组列出账户 + 最新月结余 + 更新按钮
    - entries 类 : 按收入/支出分组列出条目 + 最新月记录 + 更新按钮
    - other 类   : 结构待解析提示
  /sheet/<name>/update  : POST 保存更新后的条目值
  /sheet/<name>/add-field : POST 添加新条目/账户
"""
import re
from urllib.parse import quote
from flask import (
    Blueprint, render_template, abort, redirect, url_for,
    request, flash, session, g, current_app,
)
from sqlalchemy import select, func, desc

from .. import db
from ..models import (
    Sheet, Account, BalanceSnapshot, Item, Entry, SheetColumn,
)

bp = Blueprint("sheets", __name__)


@bp.route("/sheet/<path:name>")
def detail(name: str):
    sheet = db.session.execute(
        select(Sheet).where(Sheet.name == name)
    ).scalars().first()
    if not sheet:
        return render_template("sheets/missing.html", name=name)

    ctx = {"sheet": sheet, "is_edit": request.args.get("mode") == "edit"}

    if sheet.kind == "balances":
        accs = db.session.execute(
            select(Account).where(
                Account.sheet == name, Account.is_active == True  # noqa: E712
            ).order_by(Account.sort_order, Account.id)
        ).scalars().all()
        # 最新月份
        latest = db.session.execute(
            select(BalanceSnapshot.year, BalanceSnapshot.month)
            .order_by(desc(BalanceSnapshot.year), desc(BalanceSnapshot.month))
            .limit(1)
        ).first()
        latest_year = latest[0] if latest else None
        latest_month = latest[1] if latest else None
        # 取每个账户最新月结余
        value_map: dict[int, float] = {}
        if latest_year:
            snaps = db.session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.year == latest_year,
                    BalanceSnapshot.month == latest_month,
                    BalanceSnapshot.account_id.in_([a.id for a in accs]),
                )
            ).scalars().all()
            for s in snaps:
                value_map[s.account_id] = float(s.value or 0)
        # 大类分组
        groups: dict[str, list] = {}
        order: list[str] = []
        for a in accs:
            gname = a.group or a.type or "其他"
            if gname not in groups:
                groups[gname] = []
                order.append(gname)
            groups[gname].append((a, value_map.get(a.id)))
        ctx.update(groups_order=order, groups=groups,
                   latest_year=latest_year, latest_month=latest_month)
        return render_template("sheets/balances.html", **ctx)

    if sheet.kind == "entries":
        # 年度账单: 解析年份, 显示该年度的条目
        m = re.search(r"(20\d{2})", name)
        year = int(m.group(1)) if m else None

        # 取最新月份的数据
        latest = db.session.execute(
            select(Entry.year, Entry.month)
            .order_by(desc(Entry.year), desc(Entry.month))
            .limit(1)
        ).first()
        latest_year = latest[0] if latest else year
        latest_month = latest[1] if latest else None

        # 加载所有活跃条目
        items_q = select(Item).where(Item.is_active == True)  # noqa: E712
        if year:
            items_q = items_q.where(Item.sheet == name)
        items_q = items_q.order_by(Item.category, Item.sort_order, Item.id)
        items = db.session.execute(items_q).scalars().all()

        # 取最新月条目值
        value_map: dict[int, float] = {}
        if latest_year and latest_month:
            entries = db.session.execute(
                select(Entry).where(
                    Entry.year == latest_year, Entry.month == latest_month,
                    Entry.item_id.in_([it.id for it in items]),
                )
            ).scalars().all()
            for e in entries:
                value_map[e.item_id] = float(e.value or 0)

        # 按收入/支出分组
        groups: dict[str, list] = {}
        order: list[str] = []
        for it in items:
            cat = it.category or "其他"
            if cat not in groups:
                groups[cat] = []
                order.append(cat)
            groups[cat].append((it, value_map.get(it.id)))

        ctx.update(groups_order=order, groups=groups,
                   latest_year=latest_year, latest_month=latest_month)
        return render_template("sheets/entries.html", **ctx)

    return render_template("sheets/other.html", **ctx)


@bp.route("/sheet/<path:name>/update", methods=["POST"])
def update(name: str):
    """保存更新后的条目/账户值 (从 sheet 详情页的编辑模式提交)"""
    sheet = db.session.execute(
        select(Sheet).where(Sheet.name == name)
    ).scalars().first()
    if not sheet:
        flash("工作表不存在", "error")
        return redirect(url_for("settings.index"))

    year = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    if not year or not month:
        from datetime import datetime
        now = datetime.now()
        year, month = now.year, now.month

    if sheet.kind == "balances":
        # 保存账户结余快照
        accounts = db.session.execute(
            select(Account).where(Account.is_active == True)  # noqa: E712
        ).scalars().all()
        existing = db.session.execute(
            select(BalanceSnapshot).where(
                BalanceSnapshot.year == year, BalanceSnapshot.month == month,
            )
        ).scalars().all()
        snap_map = {s.account_id: s for s in existing}
        saved = 0
        for a in accounts:
            raw = request.form.get(f"acc_{a.id}", "").strip()
            if not raw:
                snap = snap_map.pop(a.id, None)
                if snap:
                    db.session.delete(snap)
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            snap = snap_map.get(a.id)
            if snap:
                snap.value = val
            else:
                db.session.add(BalanceSnapshot(
                    year=year, month=month, account_id=a.id,
                    value=val, source="manual",
                ))
            saved += 1
        db.session.commit()
        flash(f"已保存 {saved} 条 {year}年{month}月 结余", "success")

    elif sheet.kind == "entries":
        # 保存条目记录
        items = db.session.execute(
            select(Item).where(Item.is_active == True)  # noqa: E712
        ).scalars().all()
        existing = db.session.execute(
            select(Entry).where(Entry.year == year, Entry.month == month)
        ).scalars().all()
        entry_map = {e.item_id: e for e in existing}
        saved = 0
        for it in items:
            raw = request.form.get(f"item_{it.id}", "").strip()
            note = request.form.get(f"note_{it.id}", "").strip()
            if not raw:
                entry = entry_map.pop(it.id, None)
                if entry:
                    db.session.delete(entry)
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            entry = entry_map.get(it.id)
            if entry:
                entry.value = val
                entry.note = note
            else:
                db.session.add(Entry(
                    year=year, month=month, item_id=it.id,
                    value=val, note=note, source="manual",
                ))
            saved += 1
        db.session.commit()
        flash(f"已保存 {saved} 条 {year}年{month}月 记录", "success")

    return redirect(url_for("sheets.detail", name=name))


@bp.route("/sheet/<path:name>/add-field", methods=["POST"])
def add_field(name: str):
    """从 sheet 详情页添加新条目/账户"""
    sheet = db.session.execute(
        select(Sheet).where(Sheet.name == name)
    ).scalars().first()
    if not sheet:
        flash("工作表不存在", "error")
        return redirect(url_for("settings.index"))

    field_name = request.form.get("field_name", "").strip()
    field_type = request.form.get("field_type", "").strip()
    field_owner = request.form.get("field_owner", "家庭").strip()

    if not field_name:
        flash("字段名称不能为空", "error")
        return redirect(url_for("sheets.detail", name=name, mode="edit"))

    if sheet.kind == "balances":
        group = field_type or "其他"
        acc = Account(
            name=field_name,
            type=field_type or "其他",
            owner=field_owner,
            group=group,
            sheet=name,
            sort_order=999,
            is_active=True,
        )
        db.session.add(acc)
        db.session.commit()
        flash(f"已添加账户: {field_name} ({group})", "success")

    elif sheet.kind == "entries":
        cat = field_type or "支出"
        it = Item(
            name=field_name,
            category=cat,
            owner=field_owner,
            sheet=name,
            sort_order=999,
            is_active=True,
        )
        db.session.add(it)
        db.session.commit()
        flash(f"已添加条目: {field_name} ({cat})", "success")

    return redirect(url_for("sheets.detail", name=name, mode="edit"))
