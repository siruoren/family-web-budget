"""自定义菜单视图 - 点击左侧菜单显示自定义条目组合

用户在系统配置中创建菜单并勾选条目, 点击菜单后此页面显示所选条目的
当月值 + 占比图 + 年月选择器。
"""
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g,
)
from sqlalchemy import select

from .. import db
from ..models import AccountItem, Asset, MenuItem
from ..services.analysis import (
    monthly_summary, period_series, get_available_years, last_12m_start,
)
from ..services.formula import get_all_types
from ..services.crypto import decrypt_float, get_current_user_key

bp = Blueprint("menu", __name__)


def _get_period() -> tuple[int, int]:
    now = datetime.now()
    year = request.args.get("year", session.get("sel_year", now.year), type=int)
    month = request.args.get("month", session.get("sel_month", now.month), type=int)
    session["sel_year"] = year
    session["sel_month"] = month
    return year, month


@bp.route("/menu/<int:menu_id>")
def view(menu_id):
    """自定义菜单视图 - 显示菜单选中的条目组合"""
    mi = db.session.get(MenuItem, menu_id)
    if not mi or not mi.is_active:
        flash("菜单不存在或已停用", "error")
        return redirect(url_for("dashboard.index"))

    year, month = _get_period()
    uid = g.current_user.id

    item_id_list = mi.parsed_item_ids()
    if not item_id_list:
        flash(f"菜单 '{mi.name}' 未选择任何条目, 请到系统配置中编辑", "warning")
        return redirect(url_for("settings.index") + "#menus")

    # 查询选中的条目
    items = db.session.execute(
        select(AccountItem).where(
            AccountItem.id.in_(item_id_list),
            AccountItem.is_active == True,  # noqa: E712
        ).order_by(AccountItem.type, AccountItem.sort_order, AccountItem.id)
    ).scalars().all()

    # 保持菜单中勾选的顺序
    order_map = {iid: i for i, iid in enumerate(item_id_list)}
    items.sort(key=lambda it: order_map.get(it.id, 999))

    # 当月资产值
    assets = db.session.execute(
        select(Asset).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == uid,
            Asset.account_item_id.in_(item_id_list),
        )
    ).scalars().all()
    asset_map = {a.account_item_id: a for a in assets}

    # 近 12 月趋势 (仅含选中条目)
    yf, mf = last_12m_start(year, month)
    series = _menu_series(yf, mf, year, month, uid, item_id_list)

    # 当月条目占比数据 (item-level, 按 type 分组)
    pie_data = _menu_pie_data(year, month, uid, item_id_list)

    # 各条目近 12 月均值/合计
    item_stats = _item_stats(items, uid)

    # 汇总
    key = get_current_user_key()
    total = round(sum(decrypt_float(a.value_enc, key) for a in assets), 2)

    available_years = get_available_years(uid)
    all_types = get_all_types()

    return render_template(
        "menu/view.html",
        menu=mi, year=year, month=month,
        items=items, asset_map=asset_map,
        series=series, pie_data=pie_data,
        item_stats=item_stats, total=total,
        available_years=available_years, all_types=all_types,
    )


def _menu_series(yf, mf, yt, mt, user_id, item_id_list):
    """近 N 月: 选中条目合计 (应用层解密累加)"""
    periods = _periods_range(yf, mf, yt, mt)
    q = select(
        Asset.year, Asset.month, Asset.value_enc
    ).where(
        Asset.user_id == user_id,
        Asset.account_item_id.in_(item_id_list),
    )
    rows = db.session.execute(q).all()
    key = get_current_user_key()
    by_period = {}
    for y, m, enc in rows:
        val = decrypt_float(enc, key)
        by_period[(y, m)] = by_period.get((y, m), 0.0) + val
    out = []
    for y, m in periods:
        out.append({
            "year": y, "month": m, "label": f"{y}-{m:02d}",
            "value": round(by_period.get((y, m), 0), 2),
        })
    return out


def _periods_range(yf, mf, yt, mt):
    """生成 (year, month) 连续序列"""
    out = []
    y, m = yf, mf
    while (y, m) <= (yt, mt):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _menu_pie_data(year, month, user_id, item_id_list):
    """当月选中条目的 item-level 占比数据, 按 type 分组"""
    q = select(Asset, AccountItem).join(
        AccountItem, Asset.account_item_id == AccountItem.id
    ).where(
        Asset.year == year, Asset.month == month,
        Asset.user_id == user_id,
        Asset.account_item_id.in_(item_id_list),
    )
    rows = db.session.execute(q).all()
    key = get_current_user_key()
    groups = {}
    for a, it in rows:
        val = decrypt_float(a.value_enc, key)
        groups.setdefault(it.type, []).append({
            "id": it.id, "name": it.name, "owner": it.owner,
            "value": round(val, 2),
        })
    return groups


def _item_stats(items, user_id):
    """各条目全部月份均值/合计"""
    if not items:
        return {}
    ids = [it.id for it in items]
    rows = db.session.execute(
        select(Asset.account_item_id, Asset.value_enc).where(
            Asset.user_id == user_id,
            Asset.account_item_id.in_(ids),
        )
    ).all()
    key = get_current_user_key()
    agg = {}
    for item_id, enc in rows:
        val = decrypt_float(enc, key)
        s = agg.setdefault(item_id, {"sum": 0.0, "count": 0})
        s["sum"] += val
        s["count"] += 1
    out = {}
    for item_id, s in agg.items():
        cnt = s["count"] or 1
        out[item_id] = {
            "sum": round(s["sum"], 2),
            "avg": round(s["sum"] / cnt, 2),
            "count": int(s["count"]),
        }
    return out


@bp.route("/menu/<int:menu_id>/save", methods=["POST"])
def save(menu_id):
    """保存自定义菜单页面的条目值"""
    mi = db.session.get(MenuItem, menu_id)
    if not mi:
        flash("菜单不存在", "error")
        return redirect(url_for("dashboard.index"))

    year = request.form.get("year", type=int) or datetime.now().year
    month = request.form.get("month", type=int) or datetime.now().month
    uid = g.current_user.id

    item_id_list = mi.parsed_item_ids()
    existing = db.session.execute(
        select(Asset).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == uid,
            Asset.account_item_id.in_(item_id_list),
        )
    ).scalars().all()
    asset_map = {a.account_item_id: a for a in existing}

    saved = 0
    for item_id in item_id_list:
        raw = request.form.get(f"item_{item_id}", "").strip()
        a = asset_map.get(item_id)
        if not raw:
            if a:
                db.session.delete(a)
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if a:
            a.value = val
            a.note = request.form.get(f"note_{item_id}", "").strip()
        else:
            db.session.add(Asset(
                year=year, month=month, account_item_id=item_id,
                user_id=uid, value=val,
                note=request.form.get(f"note_{item_id}", "").strip(),
                source="manual",
            ))
        saved += 1

    db.session.commit()
    flash(f"已保存 {saved} 条 {year}年{month}月 数据", "success")
    return redirect(url_for("menu.view", menu_id=menu_id,
                             year=year, month=month))
