"""
家庭记账单 Web 应用 - 应用工厂 (MVT 架构)

M (Model)    : app.models   SQLAlchemy 数据模型 (User / AccountItem / Asset / EditLock / Setting)
V (View)     : app.views     蓝图视图 (dashboard / entries / analysis / settings)
T (Template) : app.templates Jinja2 模板
Services     : app.services  上下文 / 分析 / 公式 / 锁 / CSRF
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.version = "2.0.0"

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

    # 兼容: round 强制 2 位小数
    @app.template_filter("round")
    def _round(v, n=2):
        return round(float(v or 0), n)

    # 注册蓝图 (Views) - v2 仅保留四块 + 认证
    from .views.dashboard import bp as dashboard_bp
    from .views.entries import bp as entries_bp
    from .views.analysis import bp as analysis_bp
    from .views.settings import bp as settings_bp
    from .views.auth import bp as auth_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(entries_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(auth_bp)

    # 上下文: 注入用户 / 侧边栏 / 月份选择等通用变量
    from .services.context import inject_globals, ensure_user
    app.context_processor(inject_globals)

    # CSRF 保护 (轻量实现, 无需 Flask-WTF)
    from .services.csrf import init_csrf
    init_csrf(app)

    # 每个请求前: 从 URL (?uid=) 提取 user_id, 确定当前用户 (不再弹窗输入)
    @app.before_request
    def _ensure_user_hook():
        ensure_user()

    # 认证网关: 用户解锁 + /settings 管理员门禁 + 持久 cookie 写出
    from .services.auth import init_auth
    init_auth(app)

    # 建表 + 轻量迁移 + 初始化默认用户与菜单 (账目条目默认为空)
    with app.app_context():
        from . import models  # noqa: F401  确保模型被导入
        db.create_all()
        models.ensure_schema()  # 为已存在的表补齐新增列 (password_hash / is_admin)
        _seed_defaults()
        _seed_menu()

    return app


def _seed_defaults():
    """首次启动补齐: 默认用户 (账目条目默认为空, 由用户自行导入历史数据建立)

    设计: 不预置任何 AccountItem 模板, 让用户通过
    系统配置 → 导入导出 → 历史数据导入(可下载模板) 按需建立条目。
    菜单结构(_seed_menu)仍保留 收入/支出/结余 三组导航分组。
    """
    from sqlalchemy import select
    from .models import User

    # 确保至少有一个默认用户
    user = db.session.execute(
        select(User).where(User.is_default == True)  # noqa: E712
    ).scalars().first()
    if not user:
        any_user = db.session.execute(
            select(User).order_by(User.id).limit(1)
        ).scalars().first()
        user = any_user or User(name="家庭", is_default=True, sort_order=0)
        if not any_user:
            db.session.add(user)
    db.session.commit()


def _seed_menu():
    """首次启动补齐: 默认左侧菜单结构 (收入/支出/结余 三组)"""
    from .models import MenuItem

    if db.session.query(MenuItem).first():
        return

    menus = [
        ("收入", "", "", 0, True),
        ("家庭收入", "收入", "家庭", 1, True),
        ("支出", "", "", 2, True),
        ("家庭支出", "支出", "家庭", 3, True),
        ("结余", "", "", 4, True),
        ("家庭结余", "结余", "家庭", 5, True),
    ]
    parents = {}
    for name, ftype, fowner, order, active in menus:
        mi = MenuItem(
            name=name, sort_order=order, is_active=active,
            filter_type=ftype, filter_owner=fowner,
        )
        db.session.add(mi)
        db.session.flush()
        if not ftype and not fowner:
            parents[name] = mi.id
        else:
            parent_name = f"{fowner}{name}"
            if parent_name in parents:
                mi.parent_id = parents[parent_name]
            elif ftype in parents:
                mi.parent_id = parents[ftype]
    db.session.commit()
