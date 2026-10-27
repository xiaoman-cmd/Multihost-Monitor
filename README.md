# MultiHost Monitor · V1.0 Phase 01

FastAPI + SQLAlchemy 2.0 + Alembic + SQLite WAL，React + TypeScript + Vite + Tailwind。当前提供数据库基础、安全管理员会话、健康检查和空白控制台。

## 快速启动

需要 Python 3.12+、uv、Node.js 18+ 和 npm（建议 Node.js 22 LTS）。在仓库根目录：

```sh
cp .env.example .env
# 编辑 .env：设置至少 12 字符的唯一 MHM_ADMIN_PASSWORD
./deploy/dev.sh
```

访问 http://127.0.0.1:8000 ，用户名由 `MHM_ADMIN_USERNAME` 设置，默认 `admin`。启动脚本构建前端、迁移数据库，然后以单个 Uvicorn 进程提供完整应用；源码修改后需重新运行脚本。禁止的 Docker、Redis、Celery 均未引入。

首次启动仅创建管理员，不创建主机或指标。后续启动校验配置中的用户名／密码与数据库一致，不静默重置管理员。数据库必须先迁移，应用不会用 `create_all` 自动建表。`data/` 和 `.env` 不入版本控制；开发脚本以 umask 077 创建私有文件。SQLite 文件应位于本机磁盘。

## 验证

```sh
uv sync --frozen
uv run --frozen pytest -q
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
```

## 迁移

```sh
uv run --frozen alembic upgrade head
uv run --frozen alembic check
uv run --frozen alembic current
# 仅用于可丢弃数据库：降级会删除本阶段全部表及数据
uv run --frozen alembic downgrade base
```

迁移优先读取环境变量 `MHM_DATABASE_URL`，其次 `.env`，最后 `alembic.ini`。离线 SQL：`uv run --frozen alembic upgrade head --sql`。使用环境变量运行命令不应在命令行中传入密码。

## 目录

- `server/app/`：配置、ORM、SQLite 连接、安全会话、API 与静态文件服务。
- `server/migrations/`：独立、可回滚的 Alembic 初始迁移。
- `server/tests/`：真实文件 SQLite 上的迁移、约束、认证与 API 测试。
- `agent/`：仅预留边界，不包含 Agent 实现。
- `web/`：React 应用、Tailwind 样式、组件测试、前端锁文件。
- `deploy/`：单进程开发脚本及部署边界。
- `docs/`：架构、模型说明与阶段验收报告。

详见 [架构](docs/architecture.md) 和 [阶段报告](docs/phase-01-report.md)。
