"""系统配置 AJAX 无刷新 端到端测试

验证: 写路由带 X-Requested-With 头时返回 JSON {ok,msg,sections},
       sections 含受影响分片 HTML, 数据库变更生效, 且响应为 JSON 而非重定向。
"""
import os, sys, shutil, tempfile, re

# 临时库, 不碰真实数据
tmp = tempfile.mkdtemp(prefix="settings_ajax_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tmp,'t.db')}"
os.environ["SECRET_KEY"] = "settings-ajax-test"
# 关闭管理员门禁与加密, 聚焦 AJAX CRUD
os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("ENCRYPTION_PEPPER", None)

os.chdir(r"W:\family-web-budget")
sys.path.insert(0, r"W:\family-web-budget")

from app import create_app, db
from app.models import AccountItem, MenuItem, User

app = create_app("dev")
c = app.test_client()
ok = []

def check(n, cond, extra=""):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + n + (f"  {extra}" if extra else ""))


with app.app_context():
    # 种一个默认用户
    u = User(name="测试", is_default=True, sort_order=1)
    db.session.add(u); db.session.commit()
    uid = u.id

# 1. GET 设置页, 取 csrf
r = c.get("/settings/")
check("GET /settings/ 200", r.status_code == 200, str(r.status_code))
m = re.search(r'name="csrf-token" content="([^"]+)"', r.data.decode("utf-8"))
token = m.group(1) if m else ""
H = {"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": token}
check("取得 csrf token", bool(token))

def post(path, **fields):
    data = {"csrf_token": token}
    data.update(fields)
    return c.post(path, data=data, headers=H)


# 2. 新增条目 (AJAX)
r = post("/settings/items/add", name="测试条目A", type="支出", owner="家庭", note="e2e")
check("item add HTTP 200", r.status_code == 200, str(r.status_code))
j = r.get_json()
check("item add ok=true", j and j.get("ok") is True, str(j)[:80] if j else "no json")
check("item add 返回 items 分片", j and "items" in (j.get("sections") or {}))
check("item add 分片含新条目名", j and "测试条目A" in (j.get("sections") or {}).get("items", ""))
check("item add 返回 sysinfo 分片", j and "sysinfo" in (j.get("sections") or {}))
# 响应是 JSON 而非重定向
check("item add 非 Location 重定向", "Location" not in r.headers, str(dict(r.headers).get("Location")))

with app.app_context():
    it = db.session.execute(db.select(AccountItem).where(AccountItem.name == "测试条目A")).scalars().first()
    check("item add DB 已落库", it is not None)
    item_id = it.id if it else 0

# 3. 编辑条目
r = post(f"/settings/items/{item_id}/edit", name="测试条目A改", type="收入", owner="家庭", note="改", is_active="on", sort_order=5)
j = r.get_json()
check("item edit ok=true", j and j.get("ok") is True)
check("item edit 分片含新名", j and "测试条目A改" in (j.get("sections") or {}).get("items", ""))

# 4. 删除条目
r = post(f"/settings/items/{item_id}/delete")
j = r.get_json()
check("item delete ok=true", j and j.get("ok") is True)
check("item delete 分片不再含旧名", j and "测试条目A改" not in (j.get("sections") or {}).get("items", ""))

# 5. 菜单新增
r = post("/settings/menus/add", name="e2e菜单", filter_type="支出", sort_order=1)
j = r.get_json()
check("menu add ok", j and j.get("ok") is True)
check("menu add 返回 menus 分片", j and "menus" in (j.get("sections") or {}))

# 6. 用户新增 (带密码)
r = post("/settings/users/add", name="新人", password="abcd", password2="abcd")
j = r.get_json()
check("user add ok", j and j.get("ok") is True)
check("user add 返回 users+sysinfo+security", j and
      {"users","sysinfo","security"} <= set((j.get("sections") or {}).keys()))
check("user add 分片含新用户", j and "新人" in (j.get("sections") or {}).get("users", ""))

# 7. 公式更新
r = post("/settings/formula", formula="上月结余+当月收入-当月结余=支出")
j = r.get_json()
check("formula update ok", j and j.get("ok") is True)
check("formula update 返回 formula 分片", j and "formula" in (j.get("sections") or {}))

# 8. 错误用例: 重复用户名
r = post("/settings/users/add", name="新人", password="abcd", password2="abcd")
j = r.get_json()
check("dup user ok=false", j and j.get("ok") is False, str(j)[:80] if j else "")
check("dup user 无 sections", j and not (j.get("sections") or {}))
check("dup user 有 msg", j and bool(j.get("msg")))

# 9. 错误用例: 条目名称类型为空 (服务端校验)
r = post("/settings/items/add", name="", type="", owner="家庭")
j = r.get_json()
check("empty item ok=false", j and j.get("ok") is False)

# 9.5 账户类型管理
# 默认应有 收入/支出/结余 三个类型 (create_app._seed_defaults 植入)
r = c.get("/settings/")
html = r.data.decode("utf-8")
check("设置页含账户类型分片", 'data-section="types"' in html)
check("设置页左侧含账户类型导航", "账户类型" in html)
check("默认类型含收入/支出/结余", "收入" in html and "支出" in html and "结余" in html)
check("条目表单类型为select", '<select name="type"' in html)

# 9.55 月末结余默认条目植入 (create_app._seed_defaults 应植入)
with app.app_context():
    bal = db.session.execute(db.select(AccountItem).where(
        AccountItem.name == "月末结余",
        AccountItem.type == "结余",
        AccountItem.owner == "家庭",
    )).scalars().first()
    check("默认植入月末结余条目", bal is not None,
          f"id={bal.id if bal else None}")
    check("月末结余 is_active=True", bal is not None and bal.is_active)
    # 重复植入不应产生第二条 (幂等性)
    cnt = db.session.execute(db.select(db.func.count(AccountItem.id)).where(
        AccountItem.name == "月末结余",
        AccountItem.type == "结余",
    )).scalar()
    check("月末结余唯一不重复", cnt == 1, f"count={cnt}")

# entries 页 GET 验证渲染: 行 data-type=结余 + 分组合计 4 个 span
r = c.get(f"/entries?uid={uid}")
ehtml = r.data.decode("utf-8")
check("entries 页含月末结余行", "月末结余" in ehtml and 'data-type="结余"' in ehtml)
check("entries 页含分组合计 span",
      'id="sumIncome"' in ehtml and 'id="sumExpense"' in ehtml
      and 'id="sumBalance"' in ehtml and 'id="sumNet"' in ehtml)
check("entries 页 tfoot 含分组合计文案", "分组合计" in ehtml)

# 新增类型
r = post("/settings/types/add", name="投资")
j = r.get_json()
check("type add ok=true", j and j.get("ok") is True, str(j)[:80] if j else "")
check("type add 返回 types 分片", j and "types" in (j.get("sections") or {}))
check("type add 分片含投资", j and "投资" in (j.get("sections") or {}).get("types", ""))
check("type add 返回 items+menus+formula+sysinfo", j and
      {"types","items","menus","formula","sysinfo"} <= set((j.get("sections") or {}).keys()))

# 重复类型
r = post("/settings/types/add", name="投资")
j = r.get_json()
check("dup type ok=false", j and j.get("ok") is False)

# 删除被引用类型应阻止 (上面第2步建了"测试条目A" type=支出, 但已被删; 重建一个引用支出)
post("/settings/items/add", name="支出测试", type="支出", owner="家庭")
r = post("/settings/types/add", name="待删类型")
j = r.get_json()
check("type add 待删 ok", j and j.get("ok") is True)
# 找到"待删类型"的 id
from app.models import ItemType
with app.app_context():
    t_del = db.session.execute(db.select(ItemType).where(ItemType.name == "待删类型")).scalars().first()
    t_id_del = t_del.id if t_del else 0
    t_zc = db.session.execute(db.select(ItemType).where(ItemType.name == "支出")).scalars().first()
    t_id_zc = t_zc.id if t_zc else 0

# 删除"支出"类型应被阻止 (有条目引用)
r = post(f"/settings/types/{t_id_zc}/delete")
j = r.get_json()
check("del type 被引用阻止 ok=false", j and j.get("ok") is False, str(j)[:80] if j else "")
check("del type 阻止 msg 含引用数", j and "引用" in (j.get("msg") or ""))

# 删除无引用类型应成功
r = post(f"/settings/types/{t_id_del}/delete")
j = r.get_json()
check("del type 无引用 ok=true", j and j.get("ok") is True)
check("del type 分片不再含待删", j and "待删类型" not in (j.get("sections") or {}).get("types", ""))

# 编辑类型改名级联条目 (新建类型 + 条目引用, 改名后条目 type 跟着变)
post("/settings/types/add", name="储蓄")
with app.app_context():
    t_save = db.session.execute(db.select(ItemType).where(ItemType.name == "储蓄")).scalars().first()
    t_save_id = t_save.id
post("/settings/items/add", name="储蓄A", type="储蓄", owner="家庭")
r = post(f"/settings/types/{t_save_id}/edit", name="定期储蓄", is_active="on", sort_order=10)
j = r.get_json()
check("type edit 改名 ok=true", j and j.get("ok") is True)
with app.app_context():
    it_s = db.session.execute(db.select(AccountItem).where(AccountItem.name == "储蓄A")).scalars().first()
    check("type edit 级联条目 type", it_s is not None and it_s.type == "定期储蓄",
          f"type={it_s.type if it_s else None}")
    t_ren = db.session.execute(db.select(ItemType).where(ItemType.name == "定期储蓄")).scalars().first()
    check("type edit 新名存在", t_ren is not None)

# 10. 非 AJAX (无 X-Requested-With) 仍走 flash+redirect, 3xx
r2 = c.post("/settings/items/add", data={"csrf_token": token, "name":"传统条目","type":"支出","owner":"家庭"})
check("非AJAX 写操作 3xx 重定向", 300 <= r2.status_code < 400, str(r2.status_code))

print("\n通过", sum(ok), "/", len(ok))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok) else 1)
