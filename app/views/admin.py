"""后台数据迁移视图 - 整库导入导出 + 历史日志"""
import os
import tempfile
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    send_file, current_app,
)
from werkzeug.utils import secure_filename
from sqlalchemy import select, func

from .. import db
from ..models import ImportLog, Item, Entry, Account, BalanceSnapshot
from ..services.exporter import (
    export_sqlite, import_sqlite_db, import_json_file,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _allowed(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


@bp.route("/")
def index():
    """后台首页 - 数据概览 + 迁移工具"""
    stats = {
        "items": db.session.query(func.count(Item.id)).scalar(),
        "entries": db.session.query(func.count(Entry.id)).scalar(),
        "accounts": db.session.query(func.count(Account.id)).scalar(),
        "snapshots": db.session.query(func.count(BalanceSnapshot.id)).scalar(),
        "imports": db.session.query(func.count(ImportLog.id)).scalar(),
    }
    logs = db.session.execute(
        select(ImportLog).order_by(ImportLog.imported_at.desc()).limit(30)
    ).scalars().all()
    return render_template("admin/index.html", stats=stats, logs=logs)


# -------------------------------------------------------------- 整库导出
@bp.route("/export/db")
def export_db():
    """导出整库 SQLite (整库迁移)"""
    name = "family_budget_full.db"
    tmp = os.path.join(tempfile.gettempdir(), f"exp_{name}")
    export_sqlite(tmp, year=None, month=None)
    return send_file(
        tmp, mimetype="application/octet-stream",
        as_attachment=True, download_name=name,
    )


# -------------------------------------------------------------- 整库导入
@bp.route("/import/db", methods=["POST"])
def import_db():
    f = request.files.get("file")
    strategy = request.form.get("strategy", "skip")
    if not f or not f.filename:
        flash("请选择 .db / .sqlite / .json 文件", "error")
        return redirect(url_for("admin.index"))
    if not _allowed(f.filename):
        flash("不支持的文件类型", "error")
        return redirect(url_for("admin.index"))
    ext = f.filename.rsplit(".", 1)[-1].lower()
    fname = secure_filename(f.filename) or f"upload.{ext}"
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], fname)
    f.save(save_path)
    try:
        if ext in ("db", "sqlite", "sqlite3"):
            summary = import_sqlite_db(save_path, strategy=strategy)
            flash(
                f"导入完成: 条目 {summary['entries']} 条, "
                f"结余 {summary['balances']} 条, "
                f"去重 {summary['skipped']} 条",
                "success",
            )
        elif ext == "json":
            summary = import_json_file(save_path, strategy=strategy)
            flash(
                f"导入完成: 条目 {summary['entries']} 条, "
                f"结余 {summary['balances']} 条, "
                f"去重 {summary['skipped']} 条",
                "success",
            )
        else:
            flash("暂不支持该格式", "error")
    except Exception as e:  # noqa: BLE001
        flash(f"导入失败: {e}", "error")
    return redirect(url_for("admin.index"))


# -------------------------------------------------------------- 清空 (危险)
@bp.route("/reset", methods=["POST"])
def reset():
    """清空所有动态数据 (保留条目/账户模板)"""
    confirm = request.form.get("confirm", "").strip()
    if confirm != "确认清空":
        flash("请输入 '确认清空' 以确认", "error")
        return redirect(url_for("admin.index"))
    for tbl in (Entry, BalanceSnapshot, ImportLog):
        for row in db.session.execute(select(tbl)).scalars().all():
            db.session.delete(row)
    db.session.commit()
    flash("已清空所有动态数据 (条目 / 结余 / 日志)", "success")
    return redirect(url_for("admin.index"))
