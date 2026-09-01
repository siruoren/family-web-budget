"""工作表视图 - 左侧大菜单点击后的 sheet 详情页

  /sheet/<name>  : 展示该工作表当月记录 (支持 ?year=&month= 切换)
    - balances 类: 按大类分组列出账户 + 当月结余 + 更新按钮
    - entries 类 : 按收入/支出分组列出条目 + 当月记录 + 更新按钮
    - other 类   : 结构待解析提示
  /sheet/<name>/update  : POST 保存更新后的条目值
  /sheet/<name>/add-field : POST 添加新条目/账户
"""
import re
from datetime import datetime
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


def _get_period(name: str):
    """从 query 参数读取 year/month, 默认当月; 同时保留 mode 参数"""
    now = datetime.now()
    year = request.args.get("year", type=int) or now.year
    month = request.args.get("month", type=int) or now.month
    return year, month


@bp.route("/sheet/<path:name>")
def detail(name: str):
    sheet = db.session.execute(
        select(Sheet).where(Sheet.name == name)
    ).scalars().first()
    if not sheet:
        return render_template("sheets/missing.html", name=name)

    year, month = _get_period(name)
    is_edit = request.args.get("mode") == "edit"
    ctx = {"sheet": sheet, "is_edit": is_edit, "year": year, "month": month}

    if sheet.kind == "balances":
        accs = db.session.execute(
            select(Account).where(
                Account.sheet == name, Account.is_active == True  # noqa: E712
            ).order_by(Account.sort_order, Account.id)
        ).scalars().all()
        # 取选中月份的结余 (按当前用户隔离)
        value_map: dict[int, float] = {}
        snaps = db.session.execute(
            select(BalanceSnapshot).where(
                BalanceSnapshot.year == year,
                BalanceSnapshot.month == month,
                BalanceSnapshot.user_id == g.user_id,
                BalanceSnapshot.account_id.in_([a.id for a in accs]) if accs else False,
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
        ctx.update(groups_order=order, groups=groups)
        return render_template("sheets/balances.html", **ctx)

    if sheet.kind == "entries":
        # 加载该 sheet 下的活跃条目
        items_q = select(Item).where(
            Item.is_active == True,  # noqa: E712
            Item.sheet == name,
        ).order_by(Item.category, Item.sort_order, Item.id)
        items = db.session.execute(items_q).scalars().all()

        # 如果该 sheet 没有条目, 尝试加载所有条目 (兼容老数据)
        if not items:
            items = db.session.execute(
                select(Item).where(Item.is_active == True)  # noqa: E712
                .order_by(Item.category, Item.sort_order, Item.id)
            ).scalars().all()

        # 取选中月份的条目值 (按当前用户隔离)
        value_map: dict[int, float] = {}
        note_map: dict[int, str] = {}
        entries = db.session.execute(
            select(Entry).where(
                Entry.year == year, Entry.month == month,
                Entry.user_id == g.user_id,
                Entry.item_id.in_([it.id for it in items]) if items else False,
            )
        ).scalars().all()
        for e in entries:
            value_map[e.item_id] = float(e.value or 0)
            note_map[e.item_id] = e.note or ""

        # 按收入/支出分组
        groups: dict[str, list] = {}
        order: list[str] = []
        for it in items:
            cat = it.category or "其他"
            if cat not in groups:
                groups[cat] = []
                order.append(cat)
            groups[cat].append((it, value_map.get(it.id), note_map.get(it.id, "")))

        ctx.update(groups_order=order, groups=groups)
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
        now = datetime.now()
        year, month = now.year, now.month

    if sheet.kind == "balances":
        accounts = db.session.execute(
            select(Account).where(
                Account.sheet == name, Account.is_active == True  # noqa: E712
            )
        ).scalars().all()
        existing = db.session.execute(
            select(BalanceSnapshot).where(
                BalanceSnapshot.year == year, BalanceSnapshot.month == month,
                BalanceSnapshot.user_id == g.user_id,
                BalanceSnapshot.account_id.in_([a.id for a in accounts]) if accounts else False,
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
                    value=val, source="manual", user_id=g.user_id,
                ))
            saved += 1
        db.session.commit()
        flash(f"已保存 {saved} 条 {year}年{month}月 结余", "success")

    elif sheet.kind == "entries":
        items = db.session.execute(
            select(Item).where(
                Item.is_active == True,  # noqa: E712
                Item.sheet == name,
            )
        ).scalars().all()
        if not items:
            items = db.session.execute(
                select(Item).where(Item.is_active == True)  # noqa: E712
            ).scalars().all()
        existing = db.session.execute(
            select(Entry).where(
                Entry.year == year, Entry.month == month,
                Entry.user_id == g.user_id,
                Entry.item_id.in_([it.id for it in items]) if items else False,
            )
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
                    value=val, note=note, source="manual", user_id=g.user_id,
                ))
            saved += 1
        db.session.commit()
        flash(f"已保存 {saved} 条 {year}年{month}月 记录", "success")

    return redirect(url_for("sheets.detail", name=name, year=year, month=month))


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
    year = request.form.get("year", type=int) or datetime.now().year
    month = request.form.get("month", type=int) or datetime.now().month

    if not field_name:
        flash("字段名称不能为空", "error")
        return redirect(url_for("sheets.detail", name=name, mode="edit", year=year, month=month))

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

    return redirect(url_for("sheets.detail", name=name, mode="edit", year=year, month=month))


@bp.route("/sheet/<path:name>/batch-add-fields", methods=["POST"])
def batch_add_fields(name: str):
    """批量添加新条目/账户"""
    sheet = db.session.execute(
        select(Sheet).where(Sheet.name == name)
    ).scalars().first()
    if not sheet:
        flash("工作表不存在", "error")
        return redirect(url_for("settings.index"))

    year = request.form.get("year", type=int) or datetime.now().year
    month = request.form.get("month", type=int) or datetime.now().month
    
    # 解析批量输入的字段（每行一个字段）
    fields_text = request.form.get("fields_text", "").strip()
    field_type = request.form.get("field_type", "").strip()
    field_owner = request.form.get("field_owner", "家庭").strip()
    
    if not fields_text:
        flash("字段内容不能为空", "error")
        return redirect(url_for("sheets.detail", name=name, mode="edit", year=year, month=month))
    
    # 按行分割字段
    field_names = [line.strip() for line in fields_text.split('\n') if line.strip()]
    
    if not field_names:
        flash("有效的字段名称不能为空", "error")
        return redirect(url_for("sheets.detail", name=name, mode="edit", year=year, month=month))
    
    added_count = 0
    skipped_count = 0
    
    if sheet.kind == "balances":
        group = field_type or "其他"
        for field_name in field_names:
            # 检查是否已存在同名账户
            existing = db.session.execute(
                select(Account).where(
                    Account.sheet == name,
                    Account.name == field_name,
                    Account.is_active == True
                )
            ).scalars().first()
            
            if existing:
                skipped_count += 1
                continue
                
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
            added_count += 1
            
    elif sheet.kind == "entries":
        cat = field_type or "支出"
        for field_name in field_names:
            # 检查是否已存在同名条目
            existing = db.session.execute(
                select(Item).where(
                    Item.sheet == name,
                    Item.name == field_name,
                    Item.is_active == True
                )
            ).scalars().first()
            
            if existing:
                skipped_count += 1
                continue
                
            it = Item(
                name=field_name,
                category=cat,
                owner=field_owner,
                sheet=name,
                sort_order=999,
                is_active=True,
            )
            db.session.add(it)
            added_count += 1
    
    db.session.commit()
    
    message = f"批量添加完成: 成功 {added_count} 个"
    if skipped_count > 0:
        message += f", 跳过 {skipped_count} 个 (已存在)"
    flash(message, "success")
    
    return redirect(url_for("sheets.detail", name=name, mode="edit", year=year, month=month))
