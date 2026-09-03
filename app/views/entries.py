"""月度条目视图 - 填写条目 / 批量保存 / 并发锁

v2 架构: 使用 AccountItem + Asset 模型
"""
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g,
)
from sqlalchemy import select

from .. import db
from ..models import AccountItem, Asset, EditLock
from ..services.formula import calculate_month, get_all_types
from ..services.analysis import (
    period_series, monthly_summary, missing_items, _prev_month,
    get_available_years, last_12m_start,
)
from ..services.locking import (
    acquire_lock, release_lock, heartbeat_lock, list_locks,
    get_lock_ttl, is_lock_enabled, check_conflict,
)

bp = Blueprint("entries", __name__)


def _get_period() -> tuple[int, int]:
    now = datetime.now()
    year = request.args.get("year", session.get("sel_year", now.year), type=int)
    month = request.args.get("month", session.get("sel_month", now.month), type=int)
    session["sel_year"] = year
    session["sel_month"] = month
    return year, month


@bp.route("/entries")
def index():
    """月度条目填写页 - 顶部图表 + Excel 表单 + 锁状态"""
    year, month = _get_period()
    uid = g.current_user.id

    item_type = request.args.get("type", "")
    owner = request.args.get("owner", "")

    q = select(AccountItem).where(
        AccountItem.is_active == True  # noqa: E712
    )
    if item_type:
        q = q.where(AccountItem.type == item_type)
    if owner:
        q = q.where(AccountItem.owner == owner)
    q = q.order_by(AccountItem.type, AccountItem.sort_order, AccountItem.id)
    all_items = db.session.execute(q).scalars().all()

    assets = db.session.execute(
        select(Asset).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == uid,
        )
    ).scalars().all()
    asset_map = {a.account_item_id: a for a in assets}

    calc = calculate_month(year, month, uid)
    all_types = get_all_types()

    locks = list_locks("asset", year, month)

    # ---- 顶部图表数据 ----
    # 近 12 个月趋势
    yf, mf = last_12m_start(year, month)
    series = period_series(yf, mf, year, month, uid)

    # 当月类型占比 + 量化汇总
    summary = monthly_summary(year, month, uid)
    totals = summary["totals"]
    pie_data = summary["groups"]  # {type: [{item_id,name,value,...}, ...]}

    # 未填写条目 (按当前筛选范围)
    missing = _missing_for_scope(year, month, uid, item_type, owner)

    # 各条目近 12 月均值/合计 (用于表格底部量化条目)
    item_stats = _item_stats(all_items, yf, mf, year, month, uid)

    # 年份下拉列表 (数据库中所有年份 + 当前年)
    available_years = get_available_years(uid)

    return render_template(
        "entries/index.html", year=year, month=month,
        all_items=all_items, asset_map=asset_map,
        calc=calc, all_types=all_types,
        current_type=item_type, current_owner=owner,
        locks=locks, my_user_id=str(uid),
        series=series, totals=totals, pie_data=pie_data,
        missing=missing, item_stats=item_stats, summary=summary,
        available_years=available_years,
    )


def _missing_for_scope(year, month, user_id, item_type, owner):
    """当前筛选范围内未填写条目"""
    all_missing = missing_items(year, month, user_id)
    out = []
    for m in all_missing:
        if item_type and m["type"] != item_type:
            continue
        if owner and m["owner"] != owner:
            continue
        out.append(m)
    return out


def _item_stats(items, yf, mf, yt, mt, user_id):
    """各条目区间均值/合计/趋势 (应用层解密累加)"""
    from sqlalchemy import select
    from ..services.crypto import decrypt_float, get_current_user_key
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
    agg: dict[int, dict] = {}
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


@bp.route("/entries/save", methods=["POST"])
def save():
    """批量保存月度条目"""
    year = request.form.get("year", type=int) or datetime.now().year
    month = request.form.get("month", type=int) or datetime.now().month
    uid = g.current_user.id

    q = select(AccountItem).where(
        AccountItem.is_active == True  # noqa: E712
    )
    item_type = request.form.get("filter_type", "")
    owner = request.form.get("filter_owner", "")
    if item_type:
        q = q.where(AccountItem.type == item_type)
    if owner:
        q = q.where(AccountItem.owner == owner)
    items = db.session.execute(q).scalars().all()

    existing = db.session.execute(
        select(Asset).where(
            Asset.year == year, Asset.month == month,
            Asset.user_id == uid,
        )
    ).scalars().all()
    asset_map = {a.account_item_id: a for a in existing}

    saved, skipped, conflicts = 0, 0, []
    for it in items:
        raw = request.form.get(f"item_{it.id}", "").strip()
        note = request.form.get(f"note_{it.id}", "").strip()
        if not raw:
            a = asset_map.pop(it.id, None)
            if a:
                db.session.delete(a)
            continue
        try:
            val = float(raw)
        except ValueError:
            flash(f"条目 '{it.name}' 的值 '{raw}' 不是有效数字", "error")
            skipped += 1
            continue
        conflict = check_conflict("asset", it.id, str(uid), year, month)
        if conflict:
            conflicts.append({"name": it.name, "who": conflict["user_label"]})
            continue
        a = asset_map.get(it.id)
        if a:
            a.value = val
            a.note = note
        else:
            db.session.add(Asset(
                year=year, month=month, account_item_id=it.id,
                user_id=uid, value=val, note=note, source="manual",
            ))
        saved += 1

    db.session.commit()

    if conflicts:
        names = "、".join(f"{c['name']}(被{c['who']}锁定)" for c in conflicts)
        flash(f"以下条目被他人锁定已跳过: {names}", "warning")
    flash(f"已保存 {saved} 条 {year}年{month}月 数据" + (f", 跳过 {skipped} 条" if skipped else ""), "success")

    params = {}
    if item_type:
        params["type"] = item_type
    if owner:
        params["owner"] = owner
    params["year"] = year
    params["month"] = month
    return redirect(url_for("entries.index", **params))


# =============================================================================
# 并发锁 API
# =============================================================================

@bp.route("/locks/asset/<int:rid>", methods=["POST"])
def lock_asset(rid):
    payload = request.get_json(silent=True) or {}
    year = payload.get("year")
    month = payload.get("month")
    if not (year and month):
        return jsonify({"error": "需要 year 和 month"}), 400
    ok, holder = acquire_lock(
        "asset", rid, str(g.current_user.id), year, month,
        user_label=g.current_user.name,
    )
    if ok:
        return jsonify({"ok": True, "mine": True,
                        "remaining_seconds": get_lock_ttl(),
                        "lock_enabled": is_lock_enabled()})
    return jsonify({"ok": False, "mine": False,
                    "user_label": (holder.user_label if holder else "") or "其他用户",
                    "remaining_seconds": int((holder.expires_at - datetime.utcnow()).total_seconds()) if holder else 0}), 409


@bp.route("/locks/asset/<int:rid>/heartbeat", methods=["POST"])
def heartbeat_asset(rid):
    payload = request.get_json(silent=True) or {}
    year = payload.get("year")
    month = payload.get("month")
    if not (year and month):
        return jsonify({"error": "需要 year 和 month"}), 400
    ok, holder = heartbeat_lock(
        "asset", rid, str(g.current_user.id), year, month,
    )
    if ok:
        return jsonify({"ok": True, "mine": True,
                        "remaining_seconds": get_lock_ttl(),
                        "lock_enabled": is_lock_enabled()})
    return jsonify({"ok": False, "mine": False,
                    "user_label": (holder.user_label if holder else "") or "其他用户",
                    "remaining_seconds": int((holder.expires_at - datetime.utcnow()).total_seconds()) if holder else 0}), 409


@bp.route("/locks/asset/<int:rid>", methods=["DELETE"])
def unlock_asset(rid):
    payload = request.get_json(silent=True) or {}
    year = payload.get("year")
    month = payload.get("month")
    if not (year and month):
        return jsonify({"error": "需要 year 和 month"}), 400
    release_lock("asset", rid, str(g.current_user.id), year, month)
    return jsonify({"ok": True})


@bp.route("/locks/asset/<int:year>/<int:month>", methods=["GET"])
def list_period_locks(year, month):
    locks = list_locks("asset", year, month)
    return jsonify({
        "my_user_id": str(g.current_user.id),
        "locks": [{"rid": rid, **info} for rid, info in locks.items()],
    })
