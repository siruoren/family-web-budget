# 家庭记账单 Web 应用 (family-budget-app)

Flask + SQLAlchemy + SQLite 的家庭记账 Web 应用，支持月度收支记录、月末结余快照、Excel 导入导出、智能分析趋势预测、多用户并发编辑锁。

## 快速开始

### 本地开发

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 开发模式启动 (默认 127.0.0.1:5050)
python run.py

# 3. 生产配置启动
python run.py prod
```

### Docker 部署

```bash
# 开发环境
docker compose up -d

# 生产环境
docker compose -f docker-compose.prod.yml up -d
```

## 配置

配置优先级：环境变量 > `config.local.yml` > `config.yml` > 默认值

### config.yml

```yaml
server:
  host: "0.0.0.0"
  port: 5050
  debug: false

database:
  type: sqlite          # sqlite / mysql / postgresql
  sqlite_path: "instance/budget.db"
  # mysql: mysql+pymysql://user:pass@host:3306/dbname
  # postgresql: postgresql+psycopg2://user:pass@host:5432/dbname
  url: ""
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask 密钥 | 自动随机生成 |
| `DATABASE_URL` | 数据库连接 URI | config.yml |
| `SERVER_HOST` | 监听地址 | 127.0.0.1 |
| `SERVER_PORT` | 监听端口 | 5050 |
| `MAX_CONTENT_LENGTH` | 上传大小限制 | 50MB |

## 项目结构

```
family-web-budget/
├── app/
│   ├── __init__.py          # 应用工厂
│   ├── config.py            # 配置 (config.yml + 环境变量)
│   ├── models.py            # 数据模型
│   ├── utils.py             # 共享工具函数
│   ├── views/               # 蓝图视图 (路由)
│   ├── services/            # 业务逻辑层
│   ├── templates/           # Jinja2 模板
│   └── static/              # CSS / JS
├── config.yml               # 配置文件
├── run.py                   # 启动入口
├── Dockerfile               # Docker 构建
├── docker-compose.yml       # 开发环境编排
├── docker-compose.prod.yml  # 生产环境编排
└── requirements.txt         # Python 依赖
```

## GitFlow 分支策略

| 分支 | 用途 | 命名规范 |
|------|------|----------|
| `main` | 生产发布 | `main` |
| `develop` | 集成测试 | `develop` |
| `feature/*` | 新功能 | `feature/add-xxx` |
| `fix/*` | Bug 修复 | `fix/issue-xxx` |
| `hotfix/*` | 紧急修复 | `hotfix/critical-xxx` |
| `release/*` | 发布准备 | `release/v1.x` |

### 工作流

1. 从 `develop` 拉取 `feature/xxx` 分支开发
2. 完成后提交 PR 合并到 `develop`
3. `develop` 定期合并到 `main` 并打 tag 发布
4. 紧急修复从 `main` 拉取 `hotfix/xxx`，修复后合并回 `main` 和 `develop`

## 技术栈

- **后端**: Flask 3.x + SQLAlchemy 2.x + SQLite
- **前端**: Jinja2 模板 + Chart.js 4.x + 原生 JS
- **部署**: Docker + Gunicorn
- **CI/CD**: GitHub Actions
