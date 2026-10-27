# MultiHost Monitor V1.0 — Phase 03 阶段报告

日期：2026-10-27（Asia/Shanghai）。结论：**GO，限 Phase 03 开发及只读部署准备交付**。**正式生产部署 NO-GO／未执行**：HTTPS 入口、维护窗口和真实 Linux 集成验收仍需后续确认，不能将本报告视作上线批准。

## 1. Git 与开发前审计

- 沿用原 monorepo、FastAPI / SQLAlchemy 2.0 / Alembic / SQLite WAL、React / TypeScript / Vite / Tailwind 和 Go Agent，未重建项目或增加 Docker、Redis、Celery。
- Phase 01 基线：`f595487`。
- Phase 02 源码及报告独立候选提交：`53ff38f48a49f3bc0897550be4950b995a5c1599`。
- Phase 02-E 最小观测工具：`b13d85a3e53a6f68c20baca4a11859dc1c31e11d`；用户停止实机验收记录：`a66401e`。本阶段不把这一缺失证据改写为通过。
- Phase 03 分支：`codex/phase03-dashboard`。
- Phase 03 源码、测试、截图及原始证据提交：`ddfa464aca7a0bcb617d5c62133456d92d37cd38`。本报告后续独立提交，避免自引用提交哈希。
- 已检查 models.py、main.py、security.py、db.py、0001/0002、Phase 02 API/协议、前端登录与会话。延用 HttpOnly / SameSite 会话、Origin + CSRF 写保护、Token 摘要和主机绑定。
- 现有 Host 管理字段、AgentSample 完整 JSON、host_metrics / disk_metrics 规范字段、sample_id 和采样时间唯一约束均保留。最新指标按 sampled_at 取值，防止历史补报覆盖新数据。Agent 三个协议接口兼容。
- `.env`、数据库与 WAL、私钥／Token、Agent 本地配置、构建产物均被忽略；更改文件扫描未发现真实 Token 或私钥。0001 / 0002 与基线逐字节一致，校验和见 `docs/evidence/phase03/migration-security.json`。

## 2. 实际实现范围

| 范围 | 已实现行为 | 验收 |
|---|---|---|
| S1 总览 | 总量、在线、离线、禁用；卡片／列表；名称、云厂商、地区、系统、架构；最新 CPU、内存、磁盘、网络；最后接收时间；搜索、状态／厂商筛选、keyset 分页 | 通过 |
| 状态语义 | 中心 last_seen_at 在 90 秒内为在线；禁用独立统计；无样本不产生零值；补报历史也可显示“在线但指标过期” | API 边界与组件测试通过 |
| 刷新 | 20 秒刷新、手动刷新、暂停、隐藏页面暂停；失败保留内容并标明过期风险；15 秒请求超时、路由离开取消请求 | 通过 |
| S2 详情 | CPU、内存与 Swap、网络速率历史；磁盘／inode、接口累计量、OS、架构、版本、uptime、systemd 白名单状态与采样新鲜度 | 本机真实采集 UI 验证及测试通过 |
| 时间范围 | 1h / 6h / 24h / 7d，UTC 查询与本地时区标签，30 / 60 / 300 / 1800 秒桶 | 通过 |
| 缺口 | 采样间隔 >90 秒先分段再聚合；同桶不同段不平均；网络方向包含任一 null 则该桶保留 null；图线在 null／段切换断开 | SQL 和 SVG 组件测试通过 |
| S3 查询 | 时间限制、原始样本 timestamp+id 稳定游标和 snapshot_id、历史最多 1200 点、原始分页最多 200 行 | 通过；超限明确 422，不静默截断 |
| 保留 | 查询只读最近 7 天；后台每分钟清理、每批每表 500 行、每轮 5 秒预算；默认 dry-run CLI、显式 --apply | 临时文件库清理、外键与 WAL 测试通过 |
| S4 管理 | 创建／编辑名称、厂商、地区、标签、服务白名单；安装指引；Token 一次展示、轮换／撤销；启用／禁用 | 组件、API 与浏览器流程通过 |
| Token | 仅内存，关闭／跳转／退出／会话失效／5 分钟后清除；不保存到 localStorage、sessionStorage、URL 或日志 | 测试和源码检查通过 |
| S6 准备 | Oracle / Google 只读架构、系统、资源、服务、端口和权限检查；部署与回滚方案 | 已完成只读准备，未安装 |

未开发告警引擎、邮件通知、远程 Shell、SSH 控制、自动修复或远程服务重启。管理台的禁用只影响上报认证，不停止远程业务服务。

## 3. 数据库迁移

版本链：`0001_phase01 → 0002_phase02 → 0003_phase03`。

新增 `0003_phase03_dashboard_retention_indexes.py`：三个 sampled_at + id 清理索引；仅重建无被引用方的 agent_samples 叶表，启用 SQLite AUTOINCREMENT。原因是清理后 SQLite 普通行号可能复用，破坏分页 snapshot_id 的入库上界。已有采样 payload、ID、约束与外键保持，测试验证 downgrade 0002 再 upgrade head 后原样本仍在、且清理后新 ID 不复用。

`alembic check`：No new upgrade operations detected。全部迁移在可丢弃文件库运行，未迁移云服务器。0003 使用在线表反射，不支持完整 head 离线 SQL；正式迁移须备份并暂停写入，SQLite >=3.35。后台清理是短事务，不运行 VACUUM；物理清理积压可能跨周期，读取仍即时排除过期数据。

## 4. API 清单

所有管理读取要求原管理员会话，管理写入另要求 Origin 和 CSRF。

| 方法 | API | 用途 |
|---|---|---|
| GET | /api/v1/dashboard | 统计、筛选、分页、最新指标 |
| GET | /api/v1/hosts/{id} | 主机与完整最新样本 |
| GET | /api/v1/hosts/{id}/history | 四种范围、连续段时间桶 |
| GET | /api/v1/hosts/{id}/samples | UTC 范围、稳定原始分页 |
| PATCH | /api/v1/hosts/{id}/state | 启用／禁用 |
| POST / PUT | /api/v1/hosts ; /api/v1/hosts/{id} | 复用 Phase 02 创建／配置 |
| POST | /api/v1/hosts/{id}/token/rotate ; /token/revoke | 复用 Phase 02 凭据管理 |
| GET | /api/v1/hosts/{id}/metrics | 兼容旧查询；补充 7 天读取限制 |

`/api/v1/agent/config`、`/heartbeat`、`/metrics`，登录／会话／退出与健康接口保留。详细参数与错误契约见 `docs/phase-03-api.md`。

## 5. 实际执行测试

环境：macOS 13.1 x86_64，Python 3.12.13、Go 1.27.3 darwin/amd64；前端使用仓库锁定依赖。测试库全部临时创建，不把合成指标写入正式数据库。

| 验证 | 实际结果 | 原始输出 |
|---|---|---|
| `.venv/bin/pytest -q` | **131 passed**，23.81 秒；包含原 Phase 01/02 的 103 项及本阶段新增 28 项 | evidence/phase03/pytest.txt |
| `npm --prefix web test` | **11 passed / 3 files**，5.92 秒；原 3 项继续覆盖，新增 8 项 | evidence/phase03/web-tests.txt |
| `npm --prefix web run build` | TypeScript 无错误，Vite 构建成功，1.96 秒 | evidence/phase03/web-build.txt |
| `go test -race -count=1 -json ./...` | **19 项测试通过**，无失败包 | evidence/phase03/go-race.txt |
| `go vet ./...` / `staticcheck ./...` / `go mod verify` | 返回码均 0 | evidence/phase03/go-checks.json |
| Linux AMD64 / ARM64，CGO_ENABLED=0 构建 | 均返回码 0 | evidence/phase03/go-checks.json |
| Alembic upgrade/check/downgrade/reupgrade | 通过；含已有样本保留、约束、无 drift、FK/WAL/integrity | pytest + migration-security.json |
| 375px 浏览器 | 总览／管理／详情及创建对话框无页面横向溢出；表格局部滚动；浏览器 warn/error 为 0 | browser-checks.json + screenshots |

Python 有一条上游 Starlette TestClient/httpx 弃用警告；当前 Node 环境有 Fetch API 实验性警告和 npm 日志目录写权限提示，测试与构建退出码均 0。未为本阶段升级锁定依赖。Go race 是 macOS 本机结果，Linux 交叉构建不代表 Linux 运行通过。

新增后端覆盖：90/91 秒边界、禁用独立统计、无指标、历史补报、literal 搜索、筛选与分页、过期数据、四范围、同桶分段、网络 null、1200 点上限、snapshot 续页、认证／CSRF、7 天清理 dry-run／批次／FK，以及增量迁移和 ID 不复用。前端覆盖登录／退出、创建一次性 Token 与 CSRF、不落浏览器存储、撤销、空／旧／错误状态、刷新暂停、过滤分页、列表与 SVG 缺口。

## 6. 7 天查询性能

命令：`PYTHONPATH=server .venv/bin/python server/tools/benchmark_history.py`。工具只使用 TemporaryDirectory，构造 **1 台主机、20,160 条、30 秒间隔、7 天**的隔离合成数据；这是基准，不是真实主机验收，也不含公网 HTTPS 延迟或多主机并发容量结论。

- 10 次 history?range=7d：中位 **374.27 ms**，P95（nearest rank）**413.59 ms**。
- 聚合 20,160 条，返回 337 点，响应 92,793 bytes；查询计划使用 host_id + sampled_at 覆盖索引。
- 另行加入三表各 500 条过期行，一批清理共 1,500 行 **57.87 ms**，外键检查 0 错误。
- Web 生产 JS 179.13 kB（gzip 59.09 kB）、CSS 25.49 kB（gzip 6.28 kB）。

原始每次耗时、查询计划、平台与清理结果：`docs/evidence/phase03/history-performance.json`。这不是 Oracle 1 GiB 主机的性能测量，尚未进行生产竞争负载测试。

## 7. 真实数据与截图证据

截图来自单独临时数据库与**真实本机 macOS Go Agent**，13 条实际采样，UTC 1793098905–1793099265；没有创建云主机虚构指标。Agent 连接本机验证专用 CA 的 HTTPS；浏览器经仅回环代理访问该临时中心。该 HTTP 浏览器预览、临时非 Secure cookie 只用于隔离本机 QA，不构成生产入口方案。

禁用临时主机后，上报被拒绝；等到数据自然过期，再启用但不启动 Agent，页面显示离线与历史指标。这些状态由实际操作和时间变化产生，没有修改数据库时间伪造证据。临时服务已停止，数据库、Token、证书私钥已删除。

| 截图 | 证据 |
|---|---|
| screenshots/phase03/empty-desktop.jpg / empty-mobile.jpg | 真实空库，非零值占位指标 |
| screenshots/phase03/create-mobile.jpg | 375px 创建表单，无 Token |
| screenshots/phase03/overview-mobile.jpg | 本机真实采集总览 |
| screenshots/phase03/overview-desktop.jpg / disabled-mobile.jpg | 禁用独立统计与过期提示 |
| screenshots/phase03/stale-desktop.jpg / stale-mobile.jpg | 离线和过期历史值 |
| screenshots/phase03/detail-desktop.jpg / detail-mobile.jpg | CPU、内存、Swap、网络真实曲线与断点、分区／接口 |
| screenshots/phase03/manage-mobile.jpg | 手机管理列表 |

浏览器 375px viewport 的 document scrollWidth 为 360–375px（滚动条差异）；没有超出 viewport。详细尺寸记录见 `browser-checks.json`。上述图片不代表 Linux、Oracle、Google、ARM64 或 30 分钟稳定性验收。

## 8. 云服务器只读部署准备

实际通过 SSH 执行 `deploy/phase03-readonly.sh`，包括 uname、os-release、systemctl --version、free、df、只读 service/ss 清单、sudo -l、目录存在性检查；未创建目录、用户或 systemd unit，未安装软件、重启服务或更改网络配置。

等价复现命令（不含任何密钥内容或 Token）：

```sh
ssh -i /Users/phoenix/.ssh/flashsend_oracle/ssh-key-2026-10-02.key ubuntu@161.33.189.223 'bash -s' < deploy/phase03-readonly.sh
ssh -i ~/.ssh/google_compute_engine phoenix@34.3.99.64 'bash -s' < deploy/phase03-readonly.sh
```

| 项目 | Oracle | Google |
|---|---|---|
| 架构 | x86_64 / AMD64 | x86_64 / AMD64 |
| 系统 / systemd | Ubuntu 24.04.5 LTS / 255 | Ubuntu 26.10 / 261 |
| 内存 / available | 1,000,345,600 / 467,906,560 bytes | 997,470,208 / 534,003,712 bytes |
| 既有业务 | Caddy、FlashSend、TURN、Orbit API | sing-box、server-monitor |
| 端口冲突 | 80/443 已使用 | 443/8443 已使用 |
| 权限 | NOPASSWD sudo，未用于安装 | 同左 |

磁盘、inode、服务及监听端口原始快照保存在 `oracle-readonly.txt` / `google-readonly.txt`；Google→Oracle TCP443 可连接证据保存在 `google-to-oracle-connectivity.txt`，仅表示 TCP，不表示新监控 HTTPS 域名可用。未安装 wg 工具不能证明不存在 WireGuard；本阶段未触碰其配置。

完整待批准方案见 `docs/phase-03-deployment-plan.md`：Oracle 单 worker 回环中心 + 本机 Agent、Google 出站 Agent、Mac 管理端；独立低权限用户与目录，可信 HTTPS 入口、备份、维护迁移、安装及回滚顺序。没有在现有 443 端口覆盖已有业务。

## 9. 安全检查与遗留事项

- 管理写入继续使用 CSRF、Origin 与管理员会话；未开放 Agent 匿名注册或跨主机写入。API no-store 和 CSP 保持启用。
- Token 仅操作响应一次展示，页面内存清除，无浏览器持久化与日志输出；截图不含 Token。测试 Token 均为临时用途。
- SQLite 外键、WAL、busy_timeout 与单中心进程约束继续生效；清理白名单表、固定最短保留、受限批次、短事务，无公开删除接口。
- 合成数据只出现在测试或性能临时库；产品初次启动仍不生成主机或指标。
- **尚未完成**：Linux 双机实际 Agent 安装／systemd 与故障恢复验收、ARM64 实机、30 分钟资源测量；按用户此前要求未继续这些操作。
- **部署前待定**：监控 HTTPS 域名或安全私有入口、维护窗口、备份恢复演练、1 GiB Oracle 的真实并发与业务资源竞争评估。现有空间／内存快照不是生产容量承诺。
- 查询最多 1200 点，极碎片 7 天历史可能要求缩短范围；这是明确错误响应，未静默绘制不完整趋势。未引入持久汇总表、下采样缓存或大规模性能保证。
- 物理清理存在周期积压，删除不能恢复；上线前须确认备份。完整 head 离线 SQL 不支持，使用在线维护迁移。

**验收结论：Phase 03 开发范围 GO；生产安装与正式实机验收未执行，部署仍为 NO-GO。等待单独的正式部署验收指令。**

## 10. 修改文件完整清单

以下为 Phase 03 源码提交实际路径，另加本报告；旧阶段报告未被改写。

```text
.env.example
README.md
deploy/phase03-readonly.sh
docs/evidence/phase03/browser-checks.json
docs/evidence/phase03/go-checks.json
docs/evidence/phase03/go-race.txt
docs/evidence/phase03/google-readonly.txt
docs/evidence/phase03/google-to-oracle-connectivity.txt
docs/evidence/phase03/history-performance.json
docs/evidence/phase03/local-agent.json
docs/evidence/phase03/migration-security.json
docs/evidence/phase03/oracle-readonly.txt
docs/evidence/phase03/pytest.txt
docs/evidence/phase03/web-build.txt
docs/evidence/phase03/web-tests.txt
docs/phase-03-api.md
docs/phase-03-deployment-plan.md
docs/screenshots/phase03/create-mobile.jpg
docs/screenshots/phase03/detail-desktop.jpg
docs/screenshots/phase03/detail-mobile.jpg
docs/screenshots/phase03/disabled-mobile.jpg
docs/screenshots/phase03/empty-desktop.jpg
docs/screenshots/phase03/empty-mobile.jpg
docs/screenshots/phase03/manage-mobile.jpg
docs/screenshots/phase03/overview-desktop.jpg
docs/screenshots/phase03/overview-mobile.jpg
docs/screenshots/phase03/stale-desktop.jpg
docs/screenshots/phase03/stale-mobile.jpg
server/app/agent_api.py
server/app/config.py
server/app/dashboard_api.py
server/app/main.py
server/app/models.py
server/app/retention.py
server/migrations/versions/0003_phase03_dashboard_retention_indexes.py
server/tests/conftest.py
server/tests/test_dashboard.py
server/tools/benchmark_history.py
server/tools/preview_dashboard.py
web/src/App.test.tsx
web/src/App.tsx
web/src/Chart.test.tsx
web/src/Chart.tsx
web/src/Dashboard.test.tsx
web/src/Dashboard.tsx
web/src/HostDetail.tsx
web/src/HostForm.tsx
web/src/InstallGuide.tsx
web/src/api.ts
web/src/style.css
web/src/test-fixtures.ts
web/src/test-setup.ts
web/src/ui.tsx
web/src/useResource.ts
docs/phase-03-report.md
```
