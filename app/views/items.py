"""项目条目管理视图 - 新增 / 编辑 / 删除 / 启用禁用"""
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
)
from sqlalchemy import select, distinct

from .. import db
from ..models import Item, Account, Sheet

bp = Blueprint("items", __name__)

# 已知大类 (与 Excel 家庭结余合并单元格一致)
KNOWN_GROUPS = [
    "银行理财", "股市理财", "流动资金（李）", "流动资金（朱）",
    "外债", "信用", "其他",
]


def _derive_group(group: str, type_: str, owner: str) -> str:
    """若未填大类, 按 type+owner 推导 (流动资金+李 -> 流动资金（李）)"""
    if group:
        return group
    if type_ == "流动资金":
        return f"流动资金（{owner}）" if owner in ("李", "朱") else "流动资金"
    if type_ in ("银行理财", "股市理财", "外债"):
        return type_
    return type_ or "其他"


def _ctx_lists() -> dict:
    """表单下拉选项: 已登记 sheet 名 + 已知大类 + 已有大类"""
    sheets = list(db.session.execute(
        select(Sheet.name).order_by(Sheet.sort_order)
    ).scalars())
    groups = list(KNOWN_GROUPS)
    existing = sorted({
        g for g in db.session.execute(
            select(distinct(Account.group)).where(Account.group != "")
        ).scalars() if g
    })
    for g in existing:
        if g not in groups:
            groups.append(g)
    return {"sheets": sheets, "groups": groups}


@bp.route("/items")
def index():
    cat = request.args.get("category", "all")
    q = select(Item).order_by(Item.category, Item.sort_order, Item.id)
    if cat and cat != "all":
        q = q.where(Item.category == cat)
    items = db.session.execute(q).scalars().all()
    return render_template("items/index.html", items=items, cat=cat)


@bp.route("/items/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        item = Item(
            name=request.form["name"].strip(),
            category=request.form["category"],
            owner=request.form.get("owner", "家庭"),
            sub_category=request.form.get("sub_category", "").strip(),
            default_value=float(request.form.get("default_value") or 0),
            unit=request.form.get("unit", "元"),
            sort_order=int(request.form.get("sort_order") or 0),
            is_active=request.form.get("is_active") == "on",
            note=request.form.get("note", "").strip(),
        )
        db.session.add(item)
        db.session.commit()
        flash(f"条目 {item.name} 已创建", "success")
        return redirect(url_for("items.index"))
    return render_template("items/form.html", item=None)


@bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
def edit(item_id: int):
    item = db.session.get(Item, item_id)
    if not item:
        flash("条目不存在", "error")
        return redirect(url_for("items.index"))
    if request.method == "POST":
        item.name = request.form["name"].strip()
        item.category = request.form["category"]
        item.owner = request.form.get("owner", "家庭")
        item.sub_category = request.form.get("sub_category", "").strip()
        item.default_value = float(request.form.get("default_value") or 0)
        item.unit = request.form.get("unit", "元")
        item.sort_order = int(request.form.get("sort_order") or 0)
        item.is_active = request.form.get("is_active") == "on"
        item.note = request.form.get("note", "").strip()
        db.session.commit()
        flash("已更新", "success")
        return redirect(url_for("items.index"))
    return render_template("items/form.html", item=item)


@bp.route("/items/<int:item_id>/delete", methods=["POST"])
def delete(item_id: int):
    item = db.session.get(Item, item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
        flash("已删除", "success")
    return redirect(url_for("items.index"))


@bp.route("/items/<int:item_id>/toggle", methods=["POST"])
def toggle(item_id: int):
    item = db.session.get(Item, item_id)
    if item:
        item.is_active = not item.is_active
        db.session.commit()
        return jsonify({"ok": True, "is_active": item.is_active})
    return jsonify({"error": "not found"}), 404


# -------------------------------------------------------------- 账户管理
@bp.route("/accounts")
def accounts():
    accs = db.session.execute(
        select(Account).order_by(Account.type, Account.sort_order)
    ).scalars().all()
    return render_template("items/accounts.html", accounts=accs)


@bp.route("/accounts/new", methods=["GET", "POST"])
def account_new():
    if request.method == "POST":
        type_ = request.form.get("type", "银行理财")
        owner = request.form.get("owner", "君军之家")
        group = _derive_group(
            request.form.get("group", "").strip(), type_, owner,
        )
        acc = Account(
            name=request.form["name"].strip(),
            type=type_, owner=owner, group=group,
            sheet=request.form.get("sheet", "").strip(),
            sort_order=int(request.form.get("sort_order") or 0),
            is_active=request.form.get("is_active") == "on",
            note=request.form.get("note", "").strip(),
        )
        db.session.add(acc)
        db.session.commit()
        flash(f"账户 {acc.name} 已创建 (大类: {acc.group})", "success")
        return redirect(url_for("items.accounts"))
    return render_template("items/account_form.html", account=None, **_ctx_lists())


@bp.route("/accounts/<int:acc_id>/edit", methods=["GET", "POST"])
def account_edit(acc_id: int):
    acc = db.session.get(Account, acc_id)
    if not acc:
        flash("账户不存在", "error")
        return redirect(url_for("items.accounts"))
    if request.method == "POST":
        type_ = request.form.get("type", "银行理财")
        owner = request.form.get("owner", "君军之家")
        acc.name = request.form["name"].strip()
        acc.type = type_
        acc.owner = owner
        acc.group = _derive_group(
            request.form.get("group", "").strip(), type_, owner,
        )
        acc.sheet = request.form.get("sheet", "").strip()
        acc.sort_order = int(request.form.get("sort_order") or 0)
        acc.is_active = request.form.get("is_active") == "on"
        acc.note = request.form.get("note", "").strip()
        db.session.commit()
        flash("已更新", "success")
        return redirect(url_for("items.accounts"))
    return render_template("items/account_form.html", account=acc, **_ctx_lists())


@bp.route("/accounts/<int:acc_id>/delete", methods=["POST"])
def account_delete(acc_id: int):
    acc = db.session.get(Account, acc_id)
    if acc:
        db.session.delete(acc)
        db.session.commit()
        flash("已删除", "success")
    return redirect(url_for("items.accounts"))
