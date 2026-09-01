"""项目配置 - 家庭记账单 Web 应用 (MVT 架构)

配置优先级: 环境变量 > config.yml > 默认值
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "uploads"
EXPORT_DIR = BASE_DIR / "exports"

INSTANCE_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

CONFIG_YML = BASE_DIR / "config.yml"


def _load_yaml_config() -> dict:
    """从 config.yml 读取配置 (若文件不存在或 PyYAML 未安装则返回空 dict)"""
    if not CONFIG_YML.exists():
        return {}
    try:
        import yaml
        with open(CONFIG_YML, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}
    except Exception:
        return {}


_yaml = _load_yaml_config()


def _db_uri(yaml_cfg: dict) -> str:
    """根据 config.yml 的 database 配置生成 SQLAlchemy URI"""
    db = yaml_cfg.get("database", {})
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    db_type = db.get("type", "sqlite")
    if db_type == "sqlite":
        sqlite_path = db.get("sqlite_path", "instance/budget.db")
        return f"sqlite:///{BASE_DIR / sqlite_path}"
    if db_type in ("mysql", "postgresql"):
        url = db.get("url", "")
        if url:
            return url
    return f"sqlite:///{INSTANCE_DIR / 'budget.db'}"


class Config:
    SQLALCHEMY_DATABASE_URI = _db_uri(_yaml)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "future": True,
        "pool_size": _yaml.get("database", {}).get("pool_size", 10),
        "pool_recycle": _yaml.get("database", {}).get("pool_recycle", 3600),
    }

    SECRET_KEY = os.environ.get("SECRET_KEY") or \
        _yaml.get("security", {}).get("secret_key") or \
        "dev-secret-key-change-in-production"

    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH") or
                             _yaml.get("upload", {}).get("max_content_length", 50 * 1024 * 1024))
    UPLOAD_FOLDER = str(UPLOAD_DIR)
    ALLOWED_EXTENSIONS = set(_yaml.get("security", {}).get("allowed_extensions", [])
                             or ["xlsx", "xls", "db", "sqlite", "sqlite3", "json", "csv"])

    PER_PAGE = _yaml.get("pagination", {}).get("per_page", 50)

    # 服务端口 (run.py 读取)
    SERVER_HOST = os.environ.get("SERVER_HOST") or \
        _yaml.get("server", {}).get("host", "127.0.0.1")
    SERVER_PORT = int(os.environ.get("SERVER_PORT") or
                      _yaml.get("server", {}).get("port", 5050))

    # 示例 Excel
    SAMPLE_EXCEL = BASE_DIR / "家庭统计表2021年~20260501.xlsx"


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False


config_map = {"dev": DevConfig, "prod": ProdConfig, "default": DevConfig}
