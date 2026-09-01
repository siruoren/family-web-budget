"""月度条目视图 - 选择月份 / 在线填写 / 未填写条目高亮 / 并发锁"""
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    session, flash, g,
)
from sqlalchemy import select, and_
from sqlalchemy.orm import aliased

from .. import db
from ..models import Entry, Item, Account, BalanceSnapshot
from ..services.analysis import monthly_summary
from ..services.locking import (
    acquire_lock, release_lock, heartbeat_lock, list_locks,
    check_conflict, get_lock_ttl, is_lock_enabled,
)

bp = Blueprint("entries", __name__)


def _get_period() -> tuple[int, int]:
    """从 query/session 提取 (year, month), 默认当月"""
    now = datetime.now()
    year = request.args.get("year", session.get("sel_year", now.year), type=int)
    month = request.args.get("month", session.get("sel_month", now.month), type=int)
    session["sel_year"] = year
    session["sel_month"] = month
    return year, month


@bp.route("/entries")
def index():
    """月度条目列表 - 已填 / 未填高亮"""
    year, month = _get_period()
    summary = monthly_summary(year, month)

    # 所有启用条目 (用于检查未填写)
    all_items = db.session.execute(
        select(Item).where(Item.is_active == True).order_by(
            Item.category, Item.sort_order, Item.id
        )
    ).scalars().all()

    filled_ids = {it["item_id"] for it in summary["income_items"]}
    filled_ids |= {it["item_id"] for it in summary["expense_items"]}

    # 未填写条目
    missing_items = [
        {"id": it.id, "name": it.name, "category": it.category,
         "owner": it.owner, "sub_category": it.sub_category}
        for it in all_items if it.id not in filled_ids
    ]

    return render_template(
        "entries/index.html",
        year=year, month=month, summary=summary,
        missing_items=missing_items,
        all_items=all_items,
    )


@bp.route("/entries/edit", methods=["GET", "POST"])
def edit():
    """月度在线填写表单 - 表格化编辑所有条目"""
    year, month = _get_period()
    if request.method == "POST":
        return _save_entries(year, month)
    # 预填已有值
    all_items = db.session.execute(
        select(Item).where(Item.is_active == True).order_by(
            Item.category, Item.sort_order, Item.id
        )
    ).scalars().all()
    entries = db.session.execute(
        select(Entry).where(Entry.year == year, Entry.month == month)
    ).scalars().all()
    entry_map = {e.item_id: e for e in entries}
    # 当前周期内所有活跃锁 (供模板标记他人锁定的条目)
    locks = list_locks("entry", year, month)
    return render_template(
        "entries/edit.html", year=year, month=month,
        all_items=all_items, entry_map=entry_map,
        locks=locks, my_user_id=g.user_id,
    )


def _save_entries(year: int, month: int):
    """批量保存表单 -> 解析 form 字段 item_<id> = value

    提交时对发生变更的条目做冲突检测: 若被他人锁定则跳过并提示
    """
    saved = 0
    conflicts = []
    items = db.session.execute(
        select(Item).where(Item.is_active == True)
    ).scalars().all()
    user_id = g.user_id
    for it in items:
        val_raw = request.form.get(f"item_{it.id}", "").strip()
        note = request.form.get(f"note_{it.id}", "").strip()
        if val_raw == "":
            # 空值 -> 删除已存在的条目 (取消填写)
            existing = db.session.execute(
                select(Entry).where(
                    Entry.year == year, Entry.month == month,
                    Entry.item_id == it.id,
                )
            ).scalars().first()
            if existing:
                db.session.delete(existing)
            continue
        try:
            val = float(val_raw)
        except ValueError:
            flash(f"条目 {it.name} 的值 '{val_raw}' 不是有效数字", "error")
            continue
        # 冲突检测: 若该条目被他人锁定, 跳过并记录冲突
        conflict = check_conflict("entry", it.id, user_id, year, month)
        if conflict:
            conflicts.append({
                "id": it.id, "name": it.name,
                "who": conflict["user_label"],
            })
            continue
        existing = db.session.execute(
            select(Entry).where(
                Entry.year == year, Entry.month == month,
                Entry.item_id == it.id,
            )
        ).scalars().first()
        if existing:
            existing.value = val
            existing.note = note
        else:
            db.session.add(Entry(
                year=year, month=month, item_id=it.id,
                value=val, note=note, source="manual",
            ))
        saved += 1
    db.session.commit()
    if conflicts:
        names = "、".join(f"{c['name']}(被{c['who']}锁定)" for c in conflicts)
        flash(
            f"以下条目被他人锁定, 已跳过: {names}。请稍后刷新再试",
            "warning",
        )
    flash(f"已保存 {saved} 条 {year}年{month}月 记录", "success")
    return redirect(url_for("entries.index", year=year, month=month))


@bp.route("/entries/quick", methods=["POST"])
def quick_save():
    """单条快速保存 (AJAX)"""
    year = request.json.get("year")
    month = request.json.get("month")
    item_id = request.json.get("item_id")
    value = request.json.get("value")
    note = request.json.get("note", "")
    if not (year and month and item_id):
        return jsonify({"error": "参数缺失"}), 400
    existing = db.session.execute(
        select(Entry).where(
            Entry.year == year, Entry.month == month,
            Entry.item_id == item_id,
        )
    ).scalars().first()
    if existing:
        if value in (None, "", 0):
            db.session.delete(existing)
            db.session.commit()
            return jsonify({"ok": True, "action": "deleted"})
        existing.value = float(value)
        existing.note = note
    else:
        if value in (None, ""):
            return jsonify({"ok": True, "action": "noop"})
        db.session.add(Entry(
            year=year, month=month, item_id=item_id,
            value=float(value), note=note, source="manual",
        ))
    db.session.commit()
    return jsonify({"ok": True, "action": "saved"})


@bp.route("/balances")
def balances():
    """月度结余 (账户快照) - 选择月份查看 / 编辑"""
    year, month = _get_period()
    accounts = db.session.execute(
        select(Account).where(Account.is_active == True).order_by(
            Account.sort_order
        )
    ).scalars().all()
    snapshots = db.session.execute(
        select(BalanceSnapshot).where(
            BalanceSnapshot.year == year, BalanceSnapshot.month == month
        )
    ).scalars().all()
    snap_map = {s.account_id: s for s in snapshots}
    locks = list_locks("balance", year, month)
    return render_template(
        "entries/balances.html",
        year=year, month=month, accounts=accounts, snap_map=snap_map,
        locks=locks, my_user_id=g.user_id,
    )


@bp.route("/balances/save", methods=["POST"])
def balances_save():
    year, month = _get_period()
    accounts = db.session.execute(
        select(Account).where(Account.is_active == True)
    ).scalars().all()
    user_id = g.user_id
    saved = 0
    conflicts = []
    for a in accounts:
        raw = request.form.get(f"acc_{a.id}", "").strip()
        note = request.form.get(f"note_{a.id}", "").strip()
        if raw == "":
            existing = db.session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.year == year,
                    BalanceSnapshot.month == month,
                    BalanceSnapshot.account_id == a.id,
                )
            ).scalars().first()
            if existing:
                db.session.delete(existing)
            continue
        try:
            val = float(raw)
        except ValueError:
            flash(f"账户 {a.name} 的值 '{raw}' 不是有效数字", "error")
            continue
        # 冲突检测: 若该账户被他人锁定, 跳过并记录
        conflict = check_conflict("balance", a.id, user_id, year, month)
        if conflict:
            conflicts.append({
                "id": a.id, "name": a.name,
                "who": conflict["user_label"],
            })
            continue
        existing = db.session.execute(
            select(BalanceSnapshot).where(
                BalanceSnapshot.year == year,
                BalanceSnapshot.month == month,
                BalanceSnapshot.account_id == a.id,
            )
        ).scalars().first()
        if existing:
            existing.value = val
            existing.note = note
        else:
            db.session.add(BalanceSnapshot(
                year=year, month=month, account_id=a.id,
                value=val, note=note, source="manual",
            ))
        saved += 1
    db.session.commit()
    if conflicts:
        names = "、".join(f"{c['name']}(被{c['who']}锁定)" for c in conflicts)
        flash(
            f"以下账户被他人锁定, 已跳过: {names}。请稍后刷新再试",
            "warning",
        )
    flash(f"已保存 {saved} 条 {year}年{month}月 结余", "success")
    return redirect(url_for("entries.balances", year=year, month=month))


# =============================================================================
# 并发锁 API (条目 / 账户级, 3 分钟自动释放)
# =============================================================================

@bp.route("/locks/<resource_type>/<int:rid>/<int:year>/<int:month>", methods=["GET"])
def get_lock(resource_type, rid, year, month):
    """查询指定资源的锁状态"""
    if resource_type not in ("entry", "balance"):
        return jsonify({"error": "无效资源类型"}), 400
    # 复用 list_locks 取单个状态
    locks = list_locks(resource_type, year, month)
    info = locks.get(rid)
    if not info:
        return jsonify({"locked": False})
    # 是否被自己锁定
    mine = info["user_id"] == g.user_id
    return jsonify({
        "locked": True,
        "mine": mine,
        "user_id": info["user_id"],
        "user_label": info["user_label"],
        "remaining_seconds": info["remaining_seconds"],
    })


@bp.route("/locks/<resource_type>/<int:rid>", methods=["POST"])
def lock_resource(resource_type, rid):
    """尝试获取锁 (focus 进入输入框时触发)"""
    if resource_type not in ("entry", "balance"):
        return jsonify({"error": "无效资源类型"}), 400
    payload = request.get_json(silent=True) or {}
    year = payload.get("year")
    month = payload.get("month")
    if not (year and month):
        return jsonify({"error": "需要 year 和 month"}), 400
    ok, holder = acquire_lock(
        resource_type, rid, g.user_id, year, month,
        user_label=g.user_label,
    )
    if ok:
        ttl = get_lock_ttl()
        return jsonify({
            "ok": True, "mine": True,
            "remaining_seconds": ttl,
            "lock_enabled": is_lock_enabled(),
        })
    # 被他人持有
    return jsonify({
        "ok": False, "mine": False,
        "user_id": holder.user_id if holder else None,
        "user_label": (holder.user_label if holder else "") or "其他用户",
        "remaining_seconds": int((holder.expires_at - datetime.utcnow()).total_seconds()) if holder else 0,
    }), 409  # Conflict


@bp.route("/locks/<resource_type>/<int:rid>/heartbeat", methods=["POST"])
def heartbeat_resource(resource_type, rid):
    """心跳续期 (编辑过程中持续触发)"""
    if resource_type not in ("entry", "balance"):
        return jsonify({"error": "无效资源类型"}), 400
    payload = request.get_json(silent=True) or {}
    year = payload.get("year")
    month = payload.get("month")
    if not (year and month):
        return jsonify({"error": "需要 year 和 month"}), 400
    ok, holder = heartbeat_lock(
        resource_type, rid, g.user_id, year, month,
    )
    if ok:
        return jsonify({
            "ok": True, "mine": True,
            "remaining_seconds": get_lock_ttl(),
            "lock_enabled": is_lock_enabled(),
        })
    return jsonify({
        "ok": False, "mine": False,
        "user_label": (holder.user_label if holder else "") or "其他用户",
        "remaining_seconds": int((holder.expires_at - datetime.utcnow()).total_seconds()) if holder else 0,
    }), 409


@bp.route("/locks/<resource_type>/<int:rid>", methods=["DELETE"])
def unlock_resource(resource_type, rid):
    """释放锁 (blur 离开输入框时触发)"""
    if resource_type not in ("entry", "balance"):
        return jsonify({"error": "无效资源类型"}), 400
    payload = request.get_json(silent=True) or {}
    year = payload.get("year")
    month = payload.get("month")
    if not (year and month):
        return jsonify({"error": "需要 year 和 month"}), 400
    release_lock(resource_type, rid, g.user_id, year, month)
    return jsonify({"ok": True})


@bp.route("/locks/<resource_type>/<int:year>/<int:month>", methods=["GET"])
def list_period_locks(resource_type, year, month):
    """列出指定周期内所有活跃锁 (页面初始化批量渲染)"""
    if resource_type not in ("entry", "balance"):
        return jsonify({"error": "无效资源类型"}), 400
    locks = list_locks(resource_type, year, month)
    return jsonify({
        "my_user_id": g.user_id,
        "locks": [
            {"rid": rid, **info} for rid, info in locks.items()
        ],
    })

