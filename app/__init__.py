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

    # 注册蓝图 (Views) - v2 仅保留四块 + 认证 + 自定义菜单 + 报表
    from .views.dashboard import bp as dashboard_bp
    from .views.entries import bp as entries_bp
    from .views.analysis import bp as analysis_bp
    from .views.settings import bp as settings_bp
    from .views.auth import bp as auth_bp
    from .views.menu import bp as menu_bp
    from .views.reports import bp as reports_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(entries_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(menu_bp)
    app.register_blueprint(reports_bp)

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
    """首次启动补齐: 默认用户 + 默认账目类型 + 月末结余默认条目

    类型体系:
      收入     — 当月各项收入
      支出     — 当月各项支出
      储蓄     — 单项储蓄账户 (现金结余 / 银行卡结余 / ...)
      理财     — 投资理财账户 (股票 / 基金 / 理财产品 / ...)
      资产总和 — 月末总资产 (月末结余), 可手动输入; 有值时直接取, 无值时自动计算=储蓄+理财
    "结余" 类型为历史兼容, 新数据不再使用。
    """
    from sqlalchemy import select
    from .models import User, ItemType, AccountItem

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

    # ---- 迁移: 将旧类型名称迁移到新类型 ----
    _migrate_old_types()

    # 默认账目类型 (ItemType 表空时补齐)
    if not db.session.query(ItemType).first():
        used = db.session.execute(
            select(AccountItem.type).distinct().order_by(AccountItem.type)
        ).all()
        order = 0
        for (t,) in used:
            if t:
                order += 1
                db.session.add(ItemType(
                    name=t, sort_order=order, is_active=True,
                ))
        db.session.commit()

    # 始终确保六个默认类型存在 (收入/支出/结余/储蓄/理财/资产总和) - 已存在则跳过
    existing = {t.name for t in db.session.query(ItemType).all()}
    order = db.session.query(ItemType).count()
    for d in ["收入", "支出", "结余", "储蓄", "理财", "资产总和"]:
        if d not in existing:
            order += 1
            db.session.add(ItemType(
                name=d, sort_order=order, is_active=True,
            ))
    db.session.commit()

    # 月末结余默认条目 (type=资产总和, 可手动输入总资产)
    has_balance = db.session.execute(
        select(AccountItem.id).where(
            AccountItem.name == "月末结余",
            AccountItem.type == "资产总和",
            AccountItem.owner == "家庭",
        ).limit(1)
    ).first()
    if not has_balance:
        max_order = db.session.execute(
            select(AccountItem).where(AccountItem.type == "资产总和")
            .order_by(AccountItem.sort_order.desc()).limit(1)
        ).scalars().first()
        next_order = (max_order.sort_order + 1) if max_order else 0
        db.session.add(AccountItem(
            name="月末结余", type="资产总和", owner="家庭",
            note="月末总资产, 可手动输入; 留空则自动=储蓄+理财",
            sort_order=next_order, is_active=True,
        ))
        db.session.commit()

    # 清理: 删除冗余的"月末储蓄"条目 (已由"月末结余"替代)
    redundant = db.session.execute(
        select(AccountItem).where(
            AccountItem.name == "月末储蓄",
        )
    ).scalars().all()
    for mi in redundant:
        db.session.delete(mi)
    db.session.commit()


def _migrate_old_types():
    """迁移旧类型名称到新类型

    阶段 1: type=结余 → 储蓄/资产总和
      - "月末结余" / "军月末结余" / "君月末结余" (name 含"月末结余") → 资产总和
      - "现金结余" / "银行卡结余" 等 (type=结余 但 name 不含"月末") → 储蓄
    阶段 2: type=储蓄总和 → 资产总和 (统一重命名)
    阶段 3: ItemType 表中 "储蓄总和" → "资产总和"
    """
    from sqlalchemy import select
    from .models import AccountItem, ItemType

    # 阶段 1: type=结余 迁移
    old_jieyu = db.session.execute(
        select(AccountItem).where(AccountItem.type == "结余")
    ).scalars().all()
    for it in old_jieyu:
        if "月末结余" in it.name or "月末储蓄" in it.name:
            it.type = "资产总和"
            it.note = "月末总资产, 可手动输入; 留空则自动=储蓄+理财"
        else:
            it.type = "储蓄"
    db.session.commit()

    # 阶段 2: type=储蓄总和 → 资产总和
    old_savings_total = db.session.execute(
        select(AccountItem).where(AccountItem.type == "储蓄总和")
    ).scalars().all()
    for it in old_savings_total:
        it.type = "资产总和"
    db.session.commit()

    # 阶段 3: ItemType 重命名
    old_it = db.session.execute(
        select(ItemType).where(ItemType.name == "储蓄总和")
    ).scalars().all()
    for t in old_it:
        # 检查是否已有"资产总和"
        existing = db.session.execute(
            select(ItemType).where(ItemType.name == "资产总和")
        ).scalars().first()
        if existing:
            db.session.delete(t)
        else:
            t.name = "资产总和"
    db.session.commit()


def _seed_menu():
    """首次启动补齐: 默认左侧菜单结构 (收入/支出/储蓄/理财/资产总和 五组)"""
    from .models import MenuItem

    if db.session.query(MenuItem).first():
        return

    menus = [
        ("收入", "", "", 0, True),
        ("家庭收入", "收入", "家庭", 1, True),
        ("支出", "", "", 2, True),
        ("家庭支出", "支出", "家庭", 3, True),
        ("储蓄", "", "", 4, True),
        ("家庭储蓄", "储蓄", "家庭", 5, True),
        ("理财", "", "", 6, True),
        ("家庭理财", "理财", "家庭", 7, True),
        ("资产总和", "", "", 8, True),
        ("家庭资产总和", "资产总和", "家庭", 9, True),
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
