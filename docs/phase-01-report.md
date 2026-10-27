# MultiHost Monitor V1.0 · Phase 01 阶段报告

日期：2026-10-27（Asia/Shanghai）。结论：Phase 01 范围验收通过。

## 交付内容

已建立 server / agent / web / deploy / docs monorepo。后端包含 FastAPI 应用工厂、SQLAlchemy 2.0 模型、独立 Alembic 初始迁移、SQLite 连接配置、管理员初始化和安全会话。前端包含 React + TypeScript + Vite + Tailwind 登录页、真实主机列表读取和空白仪表盘。开发启动脚本先安装锁定依赖、构建前端和迁移，再 exec 单个 Uvicorn 进程。

11 张要求的业务表全部实现，额外包含 admin_users、admin_sessions 两张认证表；alembic_version 由 Alembic 管理。初始迁移版本为 `0001_phase01`，无业务种子数据。所有业务表升级后均为空。Agent 仅预留 README；没有采集器、模拟指标、上报接口、调度器、告警执行或通知发送。测试中的合成数据只存在于隔离的临时测试库，用于验证约束。

未引入 Docker、Redis 或 Celery。

## 验证结果

| 验证 | 结果 | 证据／范围 |
|---|---|---|
| pytest | 65 passed | API/认证/配置 30 项、数据库约束 29 项、迁移 3 项、安全辅助逻辑 3 项 |
| 前端组件测试 | 3 passed | 登录→空态→CSRF 退出、错误凭据反馈、服务离线重试 |
| TypeScript + Vite | 通过 | `npm --prefix web run build`，JS 149.04 kB / gzip 48.39 kB；CSS 10.40 kB / gzip 2.77 kB |
| 迁移升降级 | 通过 | 空库升级、重复升级保留数据、降级 base、重新升级 |
| 模型与迁移一致性 | 通过 | compare_metadata 无差异；`alembic check` 输出 No new upgrade operations detected |
| 离线迁移 SQL | 通过 | `alembic upgrade head --sql` 成功生成 SQL |
| SQLite | 通过 | 文件数据库 WAL、foreign_keys=1、busy_timeout=5000；integrity_check=ok；无外键异常 |
| 数据库约束 | 通过 | 非空／唯一／外键、采样去重、数值范围、状态集合、复合外键、恢复／发送时间一致性、活动告警去重与再次触发 |
| 认证与会话 | 通过 | Argon2id、仅存令牌摘要、Cookie 属性、Secure 配置、过期、伪造令牌、跨重启保留、登录撤销旧会话、退出撤销 |
| CSRF 与登录防护 | 通过 | 精确 Origin、CSRF token、相同错误信息、请求字段校验、线程安全限流与窗口过期 |
| 健康检查／静态服务 | 通过 | live / ready、错误版本返回 503、无私密错误泄漏、API 404、SPA 回退与路径穿越防护 |
| 单进程启动 | 通过 | 实际执行 deploy/dev.sh；最终单个 Uvicorn PID 9507，未使用 reload 或额外工作进程，验收后已关闭 |
| 浏览器验收 | 通过 | 真实登录、空白仪表盘、退出及刷新回到登录页；375px 窄屏无横向溢出；捕获的控制台 error/warn 为空 |
| 生产前端依赖审计 | 通过 | `npm --prefix web audit --omit=dev`：0 vulnerabilities；未将此结果扩展为开发依赖审计 |
| 脚本／Python 语法 | 通过 | bash -n、compileall |

验证环境：macOS、Python 3.12.13、Node.js 18.0.0、npm 8.6.0。Python 与前端依赖分别固定在 uv.lock、web/package-lock.json。Argon2 绑定限制在 26 以前，使用 25.1.0，避免当前 Intel macOS 因缺少新版 wheel 引入源码编译工具链。

测试剩余 1 条上游弃用告警：当前 Starlette TestClient 对 httpx 的使用已标记弃用，建议未来迁移 httpx2；当前测试运行成功。Alembic 路径分隔弃用告警已通过 path_separator=os 修复。

## 验收清单

- [x] 五个顶层目录齐备，依赖锁文件与环境模板齐备。
- [x] 11 张业务表及安全认证表均有 ORM 与独立迁移。
- [x] SQLite WAL、外键、busy timeout 实际生效。
- [x] 管理员登录、安全会话、退出及访问保护可用。
- [x] live / ready 健康接口可用，异常返回合适状态码。
- [x] 登录页面及无虚构数据的空白仪表盘可用。
- [x] 单进程开发脚本完成安装、构建、迁移、启动。
- [x] 迁移、约束、认证与 API 自动测试全部通过。
- [x] 未实现 Agent，未在运行期创建主机或指标演示数据。

## 已知风险与后续边界

1. 默认 HTTP + 非 Secure Cookie 仅适合本机开发；生产必须使用 HTTPS、Secure Cookie、正确 Origin，并保护 `.env` 和数据库文件。开发脚本只监听回环地址。
2. SQLite 仍是单写者，busy_timeout 不是容量保障；尚未进行负载、故障恢复或多进程验收。数据库需在本机磁盘，后续增加保留、备份和恢复策略。降级会删除本阶段全部数据。
3. 登录限流保存在单进程内存，重启清空；反向代理后的客户端共享直接连接地址配额。上线前应制定可信来源与边缘限流方案。
4. 管理员配置只在首次启动时创建账户；后续必须与数据库一致。尚无密码轮换命令、MFA 或审计日志。每个管理员仅一个活动会话，新登录会使旧浏览器退出。
5. notification_channels.config 目前为 JSON，无外部 API；正式保存通知密钥前必须增加 secret 引用或加密。没有通知发送、重试和告警计算器。
6. 主机 API 当前只返回最多 100 条，未开放 CRUD 或分页；本阶段无主机管理流程。具体告警规则作用域与服务/磁盘指纹语义由后续计算器实现。
7. 构建与单进程启动已验证；生产 TLS 反向代理、跨平台安装及浏览器自动化 CI 尚未验收。前端适配已实测桌面和 375px 窄屏，其他设备仍需后续覆盖。

## 启动与复验

从仓库根目录复制 `.env.example` 为 `.env`，设置唯一的至少 12 字符管理员密码，然后执行 `./deploy/dev.sh`。打开 http://127.0.0.1:8000 。首次启动会创建管理员，业务表保持为空。

自动复验命令见根 README。临时验收数据库仅位于 `/private/tmp`；本次未创建正式 `.env` 或正式运行数据库，也未提交任何密码。代码文件已落盘，未创建 Git 提交。

## 界面证据

- [桌面仪表盘](screenshots/dashboard-desktop.png)
- [375px 仪表盘](screenshots/dashboard-mobile.png)
- [375px 登录](screenshots/login-mobile.png)

## 文件清单

- `.env.example`
- `.gitignore`
- `README.md`
- `agent/README.md`
- `alembic.ini`
- `deploy/README.md`
- `deploy/dev.sh`
- `docs/architecture.md`
- `docs/phase-01-report.md`
- `docs/screenshots/dashboard-desktop.png`
- `docs/screenshots/dashboard-mobile.png`
- `docs/screenshots/login-mobile.png`
- `pyproject.toml`
- `server/app/__init__.py`
- `server/app/config.py`
- `server/app/db.py`
- `server/app/main.py`
- `server/app/models.py`
- `server/app/security.py`
- `server/migrations/env.py`
- `server/migrations/script.py.mako`
- `server/migrations/versions/0001_phase01_phase_01_schema.py`
- `server/tests/conftest.py`
- `server/tests/test_api.py`
- `server/tests/test_constraints.py`
- `server/tests/test_migrations.py`
- `server/tests/test_security.py`
- `uv.lock`
- `web/index.html`
- `web/package-lock.json`
- `web/package.json`
- `web/postcss.config.js`
- `web/src/App.test.tsx`
- `web/src/App.tsx`
- `web/src/main.tsx`
- `web/src/style.css`
- `web/src/test-setup.ts`
- `web/tailwind.config.js`
- `web/tsconfig.json`
- `web/vite.config.ts`
