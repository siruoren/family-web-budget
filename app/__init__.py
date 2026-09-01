"""
家庭记账单 Web 应用 - 应用工厂 (MVT 架构)

M (Model)        : app.models        SQLAlchemy 数据模型
V (View)         : app.views          蓝图视图 (路由 + 业务编排)
T (Template)     : app.templates      Jinja2 模板
Services         : app.services       Excel 解析 / 预测 / 去重 等业务逻辑
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.version = "1.1.0"

    # 加载配置
    from .config import config_map
    app.config.from_object(config_map.get(config_name, config_map["default"]))

    # 初始化数据库
    db.init_app(app)

    # 注册 Jinja 过滤器 + 全局函数: 金额格式化 ({{ x|fmt_money }} 与 {{ fmt_money(x) }} 均可用)
    def _fmt_money(v):
        v = float(v or 0)
        return f"{v:,.2f}"
    app.template_filter("fmt_money")(_fmt_money)
    app.jinja_env.globals["fmt_money"] = _fmt_money

    # 兼容: round 兼容 Jinja 内置 round, 强制 2 位小数
    @app.template_filter("round")
    def _round(v, n=2):
        return round(float(v or 0), n)

    # 注册蓝图 (Views)
    from .views.dashboard import bp as dashboard_bp
    from .views.entries import bp as entries_bp
    from .views.items import bp as items_bp
    from .views.import_export import bp as io_bp
    from .views.analysis import bp as analysis_bp
    from .views.admin import bp as admin_bp
    from .views.settings import bp as settings_bp
    from .views.sheets import bp as sheets_bp
    from .views.realtimestats import bp as realtimestats_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(entries_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(io_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(sheets_bp)
    app.register_blueprint(realtimestats_bp)

    # 上下文: 注入月份选择器等通用变量
    from .services.context import inject_globals, ensure_user
    app.context_processor(inject_globals)

    # CSRF 保护 (轻量实现, 无需 Flask-WTF)
    from .services.csrf import init_csrf
    init_csrf(app)

    # 每个请求前: 确保会话拥有唯一 user_id (用于并发锁)
    @app.before_request
    def _ensure_user_hook():
        ensure_user()

    # 建表 + 初始化默认条目
    with app.app_context():
        from . import models  # noqa: F401  确保模型被导入
        db.create_all()
        # 幂等补列: 给已有 DB 补 sheet/group 列 (create_all 不会改已存在表)
        from .services.migrations import ensure_schema
        ensure_schema()
        from .services.bootstrap import (
            ensure_default_items, ensure_default_accounts,
            ensure_structure_initialized,
        )
        ensure_default_items()
        ensure_default_accounts()
        # 首次启动: 从示例 Excel 初始化工作表登记 + 账户大类结构
        ensure_structure_initialized()

    return app
