"""导入导出视图 - Excel 历史导入 / 导出指定月份年份"""
import os
import tempfile
from datetime import datetime
from io import BytesIO
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, send_file, current_app, g,
)
from werkzeug.utils import secure_filename
from sqlalchemy import select

from .. import db
from ..models import ImportLog
from ..services.importer import import_excel
from ..services.exporter import (
    export_json, export_excel, export_sqlite, import_sqlite_db,
    import_json_file,
)
from ..utils import allowed_file

bp = Blueprint("import_export", __name__)


@bp.route("/io")
def index():
    logs = db.session.execute(
        select(ImportLog).order_by(ImportLog.imported_at.desc()).limit(50)
    ).scalars().all()
    return render_template("io/index.html", logs=logs)


# -------------------------------------------------------------- Excel 导入
@bp.route("/io/import/excel", methods=["POST"])
def import_excel_view():
    f = request.files.get("file")
    strategy = request.form.get("strategy", "skip")
    if not f or not f.filename:
        flash("请选择 Excel 文件", "error")
        return redirect(url_for("import_export.index"))
    if not allowed_file(f.filename):
        flash("不支持的文件类型", "error")
        return redirect(url_for("import_export.index"))
    fname = secure_filename(f.filename) or "upload.xlsx"
    if not fname.lower().endswith((".xlsx", ".xls")):
        fname += ".xlsx"
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], fname)
    f.save(save_path)
    try:
        summary = import_excel(save_path, strategy=strategy, user_id=g.user_id)
        flash(
            f"导入完成: 新增 {summary['total_imported']} 条, "
            f"去重跳过 {summary['total_skipped']} 条, "
            f"错误 {summary['total_error']} 条",
            "success",
        )
    except Exception as e:  # noqa: BLE001
        flash(f"导入失败: {e}", "error")
    return redirect(url_for("import_export.index"))


# -------------------------------------------------------------- 示例 Excel 一键导入
@bp.route("/io/import/sample", methods=["POST"])
def import_sample():
    sample = current_app.config["SAMPLE_EXCEL"]
    strategy = request.form.get("strategy", "skip")
    if not sample.exists():
        flash("示例 Excel 不存在: " + str(sample), "error")
        return redirect(url_for("import_export.index"))
    try:
        summary = import_excel(str(sample), strategy=strategy, user_id=g.user_id)
        flash(
            f"示例数据导入完成: 新增 {summary['total_imported']} 条, "
            f"去重 {summary['total_skipped']} 条",
            "success",
        )
    except Exception as e:  # noqa: BLE001
        flash(f"导入失败: {e}", "error")
    return redirect(url_for("import_export.index"))


# -------------------------------------------------------------- 结构初始化
@bp.route("/io/init-structure", methods=["POST"])
def init_structure():
    """从 Excel (示例或上传) 初始化 sheet 大菜单 + 账户大类结构"""
    from ..services.structure import initialize_structure_from_excel
    src = request.form.get("source", "sample")
    if src == "upload":
        f = request.files.get("file")
        if not f or not f.filename or not allowed_file(f.filename):
            flash("请上传有效的 Excel 文件", "error")
            return redirect(url_for("import_export.index"))
        fname = secure_filename(f.filename) or "upload.xlsx"
        if not fname.lower().endswith((".xlsx", ".xls")):
            fname += ".xlsx"
        save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], fname)
        f.save(save_path)
        path = save_path
    else:
        path = current_app.config["SAMPLE_EXCEL"]
        if not path.exists():
            flash("示例 Excel 不存在: " + str(path), "error")
            return redirect(url_for("import_export.index"))
    try:
        s = initialize_structure_from_excel(str(path))
        flash(
            f"结构初始化完成: 新增 sheet {s['sheets_added']} 个, "
            f"新建账户 {s['accounts_created']} 个, "
            f"回填账户 {s['accounts_backfilled']} 个, "
            f"回填条目 {s['items_backfilled']} 个",
            "success",
        )
    except Exception as e:  # noqa: BLE001
        flash(f"结构初始化失败: {e}", "error")
    return redirect(url_for("import_export.index"))


# -------------------------------------------------------------- 导出
@bp.route("/export/json")
def export_json_view():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    data = export_json(year=year, month=month, user_id=g.user_id)
    name = f"budget_{year or 'all'}-{month or 'all'}.json"
    return send_file(
        io_bytes(data), mimetype="application/json",
        as_attachment=True, download_name=name,
    )


@bp.route("/export/excel")
def export_excel_view():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    buf = export_excel(year=year, month=month, user_id=g.user_id)
    name = f"budget_{year or 'all'}-{month or 'all'}.xlsx"
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet",
        as_attachment=True, download_name=name,
    )


@bp.route("/export/sqlite")
def export_sqlite_view():
    """导出指定月份 / 年份的 SQLite 数据库"""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    name = f"budget_{year or 'all'}-{month or 'all'}.db"
    tmp = os.path.join(tempfile.gettempdir(), f"exp_{name}")
    export_sqlite(tmp, year=year, month=month)
    return send_file(
        tmp, mimetype="application/octet-stream",
        as_attachment=True, download_name=name,
    )


def io_bytes(data: bytes):
    return BytesIO(data)
