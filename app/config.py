"""项目配置 - 家庭记账单 Web 应用 (MVT 架构)"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "uploads"
EXPORT_DIR = BASE_DIR / "exports"

INSTANCE_DIR.mkdir(exist_ok=True) if not INSTANCE_DIR.exists() else None
UPLOAD_DIR.mkdir(exist_ok=True) if not UPLOAD_DIR.exists() else None
EXPORT_DIR.mkdir(exist_ok=True) if not EXPORT_DIR.exists() else None


class Config:
    # SQLite 数据库
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{INSTANCE_DIR / 'budget.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"future": True}

    SECRET_KEY = os.environ.get("SECRET_KEY", "family-budget-mvt-secret-2026")

    # 上传文件配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = str(UPLOAD_DIR)
    ALLOWED_EXTENSIONS = {"xlsx", "xls", "db", "sqlite", "sqlite3", "json", "csv"}

    # 分页
    PER_PAGE = 50

    # 示例 Excel (项目根目录) - 用于历史数据导入解析
    SAMPLE_EXCEL = BASE_DIR / "家庭统计表2021年~20260501.xlsx"


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False


config_map = {"dev": DevConfig, "prod": ProdConfig, "default": DevConfig}
