"""共享工具函数"""
from flask import current_app


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否在允许列表中"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]
